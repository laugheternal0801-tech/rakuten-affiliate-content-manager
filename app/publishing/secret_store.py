from __future__ import annotations

import ctypes
import os
import re
import sys
from collections.abc import Mapping, Sequence
from ctypes import wintypes
from threading import RLock
from typing import Protocol


class SecretStoreError(RuntimeError):
    """Raised when a secret reference cannot be resolved safely."""


class SecretStore(Protocol):
    def supports(self, reference: str) -> bool: ...

    def get(self, reference: str) -> str | None: ...

    def set(self, reference: str, value: str) -> None: ...

    def delete(self, reference: str) -> bool: ...


_ENV_REFERENCE = re.compile(r"^env://([A-Z][A-Z0-9_]*)$")
_MEMORY_REFERENCE = re.compile(r"^memory://([A-Za-z0-9][A-Za-z0-9._/-]{0,240})$")
_WINDOWS_REFERENCE = re.compile(r"^secret://windows/([A-Za-z0-9][A-Za-z0-9._/-]{0,220})$")


class EnvironmentSecretStore:
    """Read-only resolver for environment-backed credentials."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values = {key: value for key, value in (values or {}).items() if value}

    def supports(self, reference: str) -> bool:
        return bool(_ENV_REFERENCE.fullmatch(reference))

    def get(self, reference: str) -> str | None:
        match = _ENV_REFERENCE.fullmatch(reference)
        if match is None:
            raise SecretStoreError("Environment secret referenceが正しくありません。")
        name = match.group(1)
        return self._values.get(name) or os.environ.get(name) or None

    def set(self, reference: str, value: str) -> None:
        raise SecretStoreError("Environment secret storeは読み取り専用です。")

    def delete(self, reference: str) -> bool:
        raise SecretStoreError("Environment secret storeは読み取り専用です。")


class MemorySecretStore:
    """Process-local store used for tests and non-production OAuth state."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = RLock()

    def supports(self, reference: str) -> bool:
        return bool(_MEMORY_REFERENCE.fullmatch(reference))

    def _validate(self, reference: str) -> None:
        if not self.supports(reference):
            raise SecretStoreError("Memory secret referenceが正しくありません。")

    def get(self, reference: str) -> str | None:
        self._validate(reference)
        with self._lock:
            return self._values.get(reference)

    def set(self, reference: str, value: str) -> None:
        self._validate(reference)
        if not value:
            raise SecretStoreError("空の秘密値は保存できません。")
        with self._lock:
            self._values[reference] = value

    def delete(self, reference: str) -> bool:
        self._validate(reference)
        with self._lock:
            return self._values.pop(reference, None) is not None


class WindowsCredentialSecretStore:
    """Stores local secrets in Windows Credential Manager for the current user."""

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    def __init__(self, namespace: str = "note-pinterest-automation") -> None:
        if sys.platform != "win32":
            raise SecretStoreError("Windows Credential ManagerはWindowsでのみ利用できます。")
        self._namespace = namespace.strip("/")
        self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)

    def supports(self, reference: str) -> bool:
        return bool(_WINDOWS_REFERENCE.fullmatch(reference))

    def _target(self, reference: str) -> str:
        match = _WINDOWS_REFERENCE.fullmatch(reference)
        if match is None:
            raise SecretStoreError("Windows secret referenceが正しくありません。")
        return f"{self._namespace}/{match.group(1)}"

    @staticmethod
    def _credential_type() -> type[ctypes.Structure]:
        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        return Credential

    def get(self, reference: str) -> str | None:
        target = self._target(reference)
        credential_type = self._credential_type()
        pointer = ctypes.POINTER(credential_type)()
        read = self._advapi32.CredReadW
        read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        read.restype = wintypes.BOOL
        if not read(target, self._CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == self._ERROR_NOT_FOUND:
                return None
            raise SecretStoreError(f"Windows Credential Manager読込エラー（{error}）。")
        try:
            credential = pointer.contents
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return raw.decode("utf-8")
        finally:
            self._advapi32.CredFree(pointer)

    def set(self, reference: str, value: str) -> None:
        target = self._target(reference)
        if not value:
            raise SecretStoreError("空の秘密値は保存できません。")
        raw = value.encode("utf-8")
        if len(raw) > 2560:
            raise SecretStoreError("秘密値がWindows Credential Managerの上限を超えています。")
        credential_type = self._credential_type()
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = credential_type()
        credential.Type = self._CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "oauth-token"
        write = self._advapi32.CredWriteW
        write.argtypes = [ctypes.POINTER(credential_type), wintypes.DWORD]
        write.restype = wintypes.BOOL
        if not write(ctypes.byref(credential), 0):
            error = ctypes.get_last_error()
            raise SecretStoreError(f"Windows Credential Manager保存エラー（{error}）。")

    def delete(self, reference: str) -> bool:
        target = self._target(reference)
        delete = self._advapi32.CredDeleteW
        delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        delete.restype = wintypes.BOOL
        if delete(target, self._CRED_TYPE_GENERIC, 0):
            return True
        error = ctypes.get_last_error()
        if error == self._ERROR_NOT_FOUND:
            return False
        raise SecretStoreError(f"Windows Credential Manager削除エラー（{error}）。")


class CompositeSecretStore:
    def __init__(self, stores: Sequence[SecretStore]) -> None:
        self._stores = tuple(stores)
        if not self._stores:
            raise ValueError("Secret Storeを1つ以上指定してください。")

    def supports(self, reference: str) -> bool:
        return any(store.supports(reference) for store in self._stores)

    def _store(self, reference: str) -> SecretStore:
        for store in self._stores:
            if store.supports(reference):
                return store
        raise SecretStoreError("未対応のSecret referenceです。")

    def get(self, reference: str) -> str | None:
        return self._store(reference).get(reference)

    def set(self, reference: str, value: str) -> None:
        self._store(reference).set(reference, value)

    def delete(self, reference: str) -> bool:
        return self._store(reference).delete(reference)


def build_secret_store(
    *,
    x_oauth_access_token: str = "",
    meta_access_token: str = "",
    pinterest_access_token: str = "",
) -> CompositeSecretStore:
    stores: list[SecretStore] = [
        EnvironmentSecretStore(
            {
                "X_OAUTH_ACCESS_TOKEN": x_oauth_access_token,
                "META_ACCESS_TOKEN": meta_access_token,
                "PINTEREST_ACCESS_TOKEN": pinterest_access_token,
            }
        ),
        MemorySecretStore(),
    ]
    if sys.platform == "win32":
        stores.append(WindowsCredentialSecretStore())
    return CompositeSecretStore(stores)


def default_writable_secret_prefix() -> str:
    return "secret://windows" if sys.platform == "win32" else "memory://oauth"
