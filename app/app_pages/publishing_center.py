from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time
from statistics import fmean
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from app.config import get_settings
from app.creative_production.repositories import get_package, list_packages
from app.creative_production.schemas import CreativePlatform
from app.database import SessionLocal, session_scope
from app.publishing.approval import PublishingApprovalService
from app.publishing.controls import effective_control, ensure_control, set_global_pause
from app.publishing.hashing import publishing_platform
from app.publishing.performance import PerformanceNormalizer
from app.publishing.reconciliation import RECONCILIABLE_STATUSES, resolve_outcome
from app.publishing.registry import build_publisher_registry
from app.publishing.repositories import (
    acknowledge_notification,
    get_account,
    get_publication_by_key,
    list_accounts,
    list_approvals,
    list_audits,
    list_creative_performance,
    list_jobs,
    list_learning,
    list_notifications,
    list_performance_snapshots,
    list_publications,
    list_schedules,
    list_snapshots,
    list_winning_patterns,
    list_worker_heartbeats,
    list_worker_leases,
    save_account,
)
from app.publishing.scheduling import PublishingScheduler
from app.publishing.schemas import (
    ApprovalRecordStatus,
    CapabilityAvailability,
    CreativePerformanceRecord,
    NotificationStatus,
    PublishingPlatform,
    PublishingStatus,
    SocialAccountConnection,
)
from app.publishing.social_connections import (
    SocialConnectionService,
    connection_diagnostics,
)
from app.publishing.worker import PublishingWorker

PLATFORM_LABELS = {
    PublishingPlatform.X: "X",
    PublishingPlatform.INSTAGRAM: "Instagram",
    PublishingPlatform.TIKTOK: "TikTok",
    PublishingPlatform.YOUTUBE: "YouTube",
    PublishingPlatform.PINTEREST: "Pinterest",
    PublishingPlatform.REDDIT: "Reddit",
    PublishingPlatform.GENERIC: "Generic official API",
}

CREATIVE_PLATFORM_LABELS = {
    CreativePlatform.X: "X",
    CreativePlatform.INSTAGRAM_FEED: "Instagram Feed",
    CreativePlatform.INSTAGRAM_CAROUSEL: "Instagram Carousel",
    CreativePlatform.INSTAGRAM_REEL: "Instagram Reel",
    CreativePlatform.TIKTOK: "TikTok",
    CreativePlatform.YOUTUBE_LONG: "YouTube Long",
    CreativePlatform.YOUTUBE_SHORTS: "YouTube Shorts",
    CreativePlatform.PINTEREST: "Pinterest",
}

DEFAULT_SCOPES = {
    PublishingPlatform.X: ["tweet.read", "tweet.write", "users.read"],
    PublishingPlatform.INSTAGRAM: [
        "instagram_business_basic",
        "instagram_business_content_publish",
    ],
    PublishingPlatform.TIKTOK: ["video.publish"],
    PublishingPlatform.YOUTUBE: ["youtube.upload"],
    PublishingPlatform.PINTEREST: [
        "boards:read",
        "boards:write",
        "pins:read",
        "pins:write",
    ],
    PublishingPlatform.REDDIT: ["identity", "read", "submit"],
    PublishingPlatform.GENERIC: [],
}

TERMINAL_JOB_STATUSES = {
    PublishingStatus.PUBLISHED,
    PublishingStatus.DRY_RUN_COMPLETED,
    PublishingStatus.FAILED,
    PublishingStatus.BLOCKED,
    PublishingStatus.CANCELLED,
    PublishingStatus.MISSED_SCHEDULE,
    PublishingStatus.RECONCILIATION_REQUIRED,
}


def _show_error(exc: Exception) -> None:
    st.error(f"処理できませんでした: {exc}", icon=":material/error:")


def _account_label(account: SocialAccountConnection) -> str:
    return f"{account.display_name} · {account.account_id}"


def _platform_assets(package_id: str, platform: CreativePlatform) -> list[str]:
    with session_scope() as session:
        package = get_package(session, package_id)
    if package is None:
        return []
    wants_video = platform in {
        CreativePlatform.TIKTOK,
        CreativePlatform.YOUTUBE_LONG,
        CreativePlatform.YOUTUBE_SHORTS,
        CreativePlatform.INSTAGRAM_REEL,
    }
    return [
        asset.asset_id
        for asset in package.assets
        if not asset.is_placeholder
        and (
            (wants_video and asset.mime_type.startswith("video/"))
            or (not wants_video and asset.mime_type.startswith("image/"))
        )
    ]


def _set_oauth_notice(kind: str, message: str) -> None:
    st.session_state["publishing_oauth_notice"] = {"kind": kind, "message": message}


def _render_oauth_notice() -> None:
    notice = st.session_state.pop("publishing_oauth_notice", None)
    if not isinstance(notice, dict):
        return
    message = str(notice.get("message") or "")
    if notice.get("kind") == "success":
        st.success(message, icon=":material/verified:")
    else:
        st.error(message, icon=":material/error:")


def _handle_oauth_callback() -> None:
    state = str(st.query_params.get("state", ""))
    code = str(st.query_params.get("code", ""))
    provider_error = str(st.query_params.get("error", ""))
    if not state and not code and not provider_error:
        return

    service: SocialConnectionService | None = None
    try:
        service = SocialConnectionService(get_settings())
        if provider_error:
            if state:
                service.cancel_callback(state)
            _set_oauth_notice("error", "SNS側で認証がキャンセルされました。もう一度実行できます。")
        elif not state or not code:
            _set_oauth_notice("error", "OAuth callbackに必要な情報が不足しています。")
        else:
            with session_scope() as session:
                account = service.complete_callback(session, state=state, code=code)
            _set_oauth_notice(
                "success",
                f"{PLATFORM_LABELS[account.platform]}の{account.display_name}を接続しました。",
            )
    except Exception as exc:
        _set_oauth_notice("error", f"OAuth接続を完了できませんでした: {exc}")
    finally:
        if service is not None:
            service.close()
        st.query_params.clear()
    st.rerun()


def _status_header() -> None:
    settings = get_settings()
    with session_scope() as session:
        ensure_control(session, settings)
        control = effective_control(session, settings)
        jobs = list_jobs(session)
        publications = list_publications(session)
        learning_count = len(list_learning(session))

    if control.globally_paused:
        st.error("GLOBAL PAUSE中です。Dry Runを含む新規実行を停止しています。")
    elif control.dry_run or not control.publishing_enabled:
        st.warning(
            "DRY RUN / KILL SWITCH安全モードです。外部SNSへの送信は発生しません。",
            icon=":material/shield_lock:",
        )
    else:
        st.error("本番ゲートが有効です。対象アカウントと承認内容を再確認してください。")

    with st.container(horizontal=True):
        st.metric("Queue", sum(job.status not in TERMINAL_JOB_STATUSES for job in jobs))
        st.metric(
            "要照合",
            sum(
                job.status is PublishingStatus.RECONCILIATION_REQUIRED
                for job in jobs
            ),
        )
        st.metric(
            "Dry Run完了", sum(job.status is PublishingStatus.DRY_RUN_COMPLETED for job in jobs)
        )
        st.metric("外部Publication", len(publications))
        st.metric("Learning", learning_count)

    st.caption(
        "安全経路: Human approval → immutable lock → schedule approval → preflight → "
        "idempotent publish → metrics → labelled learning"
    )


def _overview_tab() -> None:
    settings = get_settings()
    with session_scope() as session:
        control = ensure_control(session, settings)
        active_approvals = list_approvals(session, active_only=True)
        snapshots = list_snapshots(session)
        schedules = list_schedules(session)
        jobs = list_jobs(session)

    st.subheader("運用状態")
    state_rows = [
        {
            "項目": "Environment publishing gate",
            "値": "ON" if settings.publishing_enabled else "OFF",
        },
        {
            "項目": "External API gate",
            "値": "ON" if settings.publishing_external_api_enabled else "OFF",
        },
        {
            "項目": "Environment Dry Run",
            "値": "ON" if settings.publishing_dry_run else "OFF",
        },
        {"項目": "Database Dry Run", "値": "ON" if control.dry_run else "OFF"},
        {"項目": "Global pause", "値": "ON" if control.globally_paused else "OFF"},
        {"項目": "Autonomy", "値": control.autonomy.value},
    ]
    st.dataframe(pd.DataFrame(state_rows), hide_index=True, width="stretch")

    st.subheader("安全制御")
    st.caption("この画面では本番投稿を有効化できません。全体停止だけを即時操作できます。")
    actor = st.text_input("操作担当者", key="publishing_pause_actor", placeholder="氏名・担当ID")
    with st.container(horizontal=True):
        if st.button(
            "全体停止",
            icon=":material/pause_circle:",
            type="primary",
            disabled=control.globally_paused,
        ):
            try:
                with session_scope() as session:
                    set_global_pause(session, True, actor, settings)
                st.rerun()
            except Exception as exc:
                _show_error(exc)
        if st.button(
            "停止解除",
            icon=":material/play_circle:",
            disabled=not control.globally_paused,
        ):
            try:
                with session_scope() as session:
                    set_global_pause(session, False, actor, settings)
                st.rerun()
            except Exception as exc:
                _show_error(exc)

    st.subheader("パイプライン件数")
    st.dataframe(
        pd.DataFrame(
            [
                {"段階": "Active approvals", "件数": len(active_approvals)},
                {"段階": "Locked snapshots", "件数": len(snapshots)},
                {"段階": "Schedules", "件数": len(schedules)},
                {"段階": "Jobs", "件数": len(jobs)},
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _accounts_tab() -> None:
    settings = get_settings()
    with session_scope() as session:
        accounts = list_accounts(session)

    st.subheader("Target Account台帳")
    st.caption(
        "ここに保存するのはアカウント識別メタデータだけです。OAuth token本体は保存・表示しません。"
    )
    if accounts:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Platform": PLATFORM_LABELS[item.platform],
                        "表示名": item.display_name,
                        "External account ID": item.account_id,
                        "状態": item.status.value,
                        "Scopes": ", ".join(item.scopes),
                        "確認日時": item.last_verified_at,
                    }
                    for item in accounts
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("Target Accountはまだ登録されていません。")

    st.subheader("X・Instagram・TikTok 接続ウィザード")
    diagnostics = connection_diagnostics(settings, accounts)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Platform": PLATFORM_LABELS[item.platform],
                    "確認項目": item.item,
                    "状態": item.status,
                    "準備": "OK" if item.ready else "操作待ち",
                }
                for item in diagnostics
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Token本体は画面・Database・監査Logに保存しません。OAuth TokenはWindows Credential "
        "Manager、DatabaseにはSecret referenceだけを保存します。"
    )

    oauth_requests = st.session_state.get("publishing_oauth_requests", {})
    if not isinstance(oauth_requests, dict):
        oauth_requests = {}
    social_platforms = (
        (PublishingPlatform.X, settings.x_oauth_configured, settings.x_redirect_uri),
        (
            PublishingPlatform.INSTAGRAM,
            settings.instagram_publishing_configured,
            settings.meta_redirect_uri,
        ),
        (
            PublishingPlatform.TIKTOK,
            settings.tiktok_oauth_configured,
            settings.tiktok_redirect_uri,
        ),
    )
    environment_tokens = {
        PublishingPlatform.X: bool(settings.x_oauth_access_token),
        PublishingPlatform.INSTAGRAM: bool(settings.meta_access_token),
        PublishingPlatform.TIKTOK: False,
    }
    for social_platform, configured, redirect_uri in social_platforms:
        label = PLATFORM_LABELS[social_platform]
        connected_count = sum(
            account.platform is social_platform
            and account.status is CapabilityAvailability.AVAILABLE
            for account in accounts
        )
        with st.container(border=True):
            st.markdown(f"**{label}** · 接続済み {connected_count}件")
            st.caption(f"Callback URL: {redirect_uri or '未設定'}")
            with st.container(horizontal=True):
                if st.button(
                    f"{label} 認証リンクを作成",
                    key=f"publishing_begin_oauth_{social_platform.value}",
                    icon=":material/link:",
                    disabled=not configured,
                ):
                    service: SocialConnectionService | None = None
                    try:
                        service = SocialConnectionService(settings)
                        request = service.begin(social_platform)
                        oauth_requests[social_platform.value] = {
                            "url": request.authorization_url,
                            "expires_at": request.expires_at.isoformat(),
                        }
                        st.session_state["publishing_oauth_requests"] = oauth_requests
                    except Exception as exc:
                        _show_error(exc)
                    finally:
                        if service is not None:
                            service.close()
                if environment_tokens[social_platform] and st.button(
                    f"既存Tokenを確認して{label}台帳へ登録",
                    key=f"publishing_connect_env_{social_platform.value}",
                    icon=":material/verified_user:",
                ):
                    service = None
                    try:
                        service = SocialConnectionService(settings)
                        with session_scope() as session:
                            account = service.connect_environment_token(session, social_platform)
                        st.success(f"{account.display_name}を確認・登録しました。")
                        st.rerun()
                    except Exception as exc:
                        _show_error(exc)
                    finally:
                        if service is not None:
                            service.close()

            request_data = oauth_requests.get(social_platform.value)
            if isinstance(request_data, dict):
                try:
                    expires_at = datetime.fromisoformat(str(request_data["expires_at"]))
                    authorization_url = str(request_data["url"])
                except (KeyError, TypeError, ValueError):
                    expires_at = datetime.now(UTC)
                    authorization_url = ""
                if authorization_url and expires_at > datetime.now(UTC):
                    st.link_button(
                        f"{label}を開いて接続を許可",
                        authorization_url,
                        type="primary",
                        icon=":material/open_in_new:",
                        width="stretch",
                    )
                    st.caption(
                        f"認証リンク有効期限: {expires_at.astimezone(ZoneInfo('Asia/Tokyo'))}"
                    )
                else:
                    oauth_requests.pop(social_platform.value, None)
                    st.session_state["publishing_oauth_requests"] = oauth_requests

    manageable = [
        account
        for account in accounts
        if account.platform
        in {
            PublishingPlatform.X,
            PublishingPlatform.INSTAGRAM,
            PublishingPlatform.TIKTOK,
        }
        and account.status is CapabilityAvailability.AVAILABLE
    ]
    if manageable:
        st.subheader("接続の更新・解除")
        selected_account = st.selectbox(
            "対象接続",
            manageable,
            format_func=lambda item: f"{PLATFORM_LABELS[item.platform]} · {_account_label(item)}",
            key="publishing_manage_social_account",
        )
        supports_refresh = bool(selected_account.metadata.get("supports_refresh")) and not (
            selected_account.credential_reference.startswith("env://")
        )
        with st.container(horizontal=True):
            if st.button(
                "本人確認・Token更新",
                key="publishing_refresh_social_account",
                icon=":material/refresh:",
                disabled=not supports_refresh,
            ):
                service = None
                try:
                    service = SocialConnectionService(settings)
                    with session_scope() as session:
                        current = get_account(session, selected_account.connection_id)
                        if current is None:
                            raise LookupError("対象接続が見つかりません。")
                        service.refresh_and_save(session, current, actor="publishing_center")
                    st.success("Token更新と本人確認が完了しました。")
                    st.rerun()
                except Exception as exc:
                    _show_error(exc)
                finally:
                    if service is not None:
                        service.close()
            if selected_account.platform is PublishingPlatform.TIKTOK and st.button(
                "TikTok Creator Infoを更新",
                key="publishing_refresh_tiktok_creator_info",
                icon=":material/sync:",
            ):
                service = None
                try:
                    service = SocialConnectionService(settings)
                    with session_scope() as session:
                        current = get_account(session, selected_account.connection_id)
                        if current is None:
                            raise LookupError("対象接続が見つかりません。")
                        service.refresh_tiktok_creator_info_and_save(
                            session,
                            current,
                            actor="publishing_center",
                        )
                    st.success("TikTok Creator Infoを更新しました。")
                    st.rerun()
                except Exception as exc:
                    _show_error(exc)
                finally:
                    if service is not None:
                        service.close()

        disconnect_actor = st.text_input(
            "解除担当者",
            key="publishing_disconnect_actor",
            placeholder="氏名・担当ID",
        )
        disconnect_confirmed = st.checkbox(
            "選択した接続を解除する",
            key="publishing_disconnect_confirmed",
        )
        revoke_remote = True
        if selected_account.platform in {
            PublishingPlatform.X,
            PublishingPlatform.TIKTOK,
        }:
            revoke_remote = st.checkbox(
                f"{PLATFORM_LABELS[selected_account.platform]}側のTokenも失効させる",
                value=True,
                key=f"publishing_disconnect_revoke_{selected_account.platform.value}",
            )
        if st.button(
            "接続を解除",
            key="publishing_disconnect_social_account",
            icon=":material/link_off:",
            disabled=not disconnect_confirmed or not disconnect_actor.strip(),
        ):
            service = None
            try:
                service = SocialConnectionService(settings)
                with session_scope() as session:
                    current = get_account(session, selected_account.connection_id)
                    if current is None:
                        raise LookupError("対象接続が見つかりません。")
                    service.disconnect(
                        session,
                        current,
                        actor=disconnect_actor,
                        revoke_remote=revoke_remote,
                    )
                st.success("接続を解除しました。")
                st.rerun()
            except Exception as exc:
                _show_error(exc)
            finally:
                if service is not None:
                    service.close()

    pending_actions = [item.required_action for item in diagnostics if not item.ready]
    with st.container(border=True):
        st.markdown("**最後にあなたが操作する項目**")
        if pending_actions:
            for index, action in enumerate(dict.fromkeys(pending_actions), start=1):
                st.write(f"{index}. {action}")
        else:
            st.success("X・Instagram・TikTokのアカウント接続準備は完了しています。")
        st.caption("本番投稿ゲートは引き続きOFFです。接続確認だけではSNS投稿は発生しません。")

    with st.expander("Pinterest既存接続の状態"):
        st.write(
            {
                "OAuth App": "設定済み" if settings.pinterest_oauth_configured else "未設定",
                "Access Token": "設定済み" if settings.pinterest_access_token else "未設定",
                "Image Pin adapter": "実装済み・停止中",
            }
        )

    st.subheader("Dry Run用アカウントを登録")
    st.caption("APIキーやOAuth接続なしで、誤アカウント防止とPreflightを試すための台帳です。")
    with st.form("publishing_dry_account_form", clear_on_submit=True):
        platform = st.selectbox(
            "Platform",
            list(PublishingPlatform),
            format_func=lambda item: PLATFORM_LABELS[item],
        )
        account_id = st.text_input("External account ID", placeholder="例: test-brand-jp")
        display_name = st.text_input("表示名", placeholder="例: ブランド公式（テスト）")
        submitted = st.form_submit_button("Dry Run台帳へ登録", icon=":material/add:")
    if submitted:
        try:
            account = SocialAccountConnection(
                platform=platform,
                account_id=account_id,
                display_name=display_name,
                status=CapabilityAvailability.DRY_RUN,
                scopes=DEFAULT_SCOPES[platform],
                metadata={"dry_run_only": True, "created_from": "publishing_center"},
            )
            with session_scope() as session:
                save_account(session, account)
            st.success("Dry Run用Target Accountを登録しました。")
            st.rerun()
        except Exception as exc:
            _show_error(exc)


def _approval_tab() -> None:
    with session_scope() as session:
        package_rows = list_packages(session, limit=100)
        accounts = list_accounts(session)
        approvals = list_approvals(session)

    st.subheader("Human Approval & Version Lock")
    st.caption(
        "承認対象のPlatform、実Asset、Target Accountを明示選択します。"
        "承認後の変更はHash検知で無効化されます。"
    )
    if not package_rows:
        st.info("AI制作スタジオでContent Packageを作成すると承認できます。")
        return

    package_options = {row.id: f"{row.id} · v{row.version} · {row.status}" for row in package_rows}
    package_id = st.selectbox(
        "Content Package",
        list(package_options),
        format_func=lambda item: package_options[item],
    )
    with session_scope() as session:
        package = get_package(session, package_id)
    if package is None:
        st.error("Content Packageを読み込めません。")
        return

    publishable = [
        platform for platform in package.final_content if publishing_platform(platform) is not None
    ]
    approve_all = st.checkbox(
        "Publishableな全Platformを対象にする",
        key=f"publishing_approve_all_{package_id}",
    )
    selected = (
        publishable
        if approve_all
        else st.multiselect(
            "承認するPlatform",
            publishable,
            format_func=lambda item: CREATIVE_PLATFORM_LABELS.get(item, item.value),
        )
    )

    account_selection: dict[CreativePlatform, str] = {}
    asset_selection: dict[CreativePlatform, list[str]] = {}
    metadata: dict[CreativePlatform, dict[str, object]] = {}
    for platform in selected:
        mapped = publishing_platform(platform)
        if mapped is None:
            continue
        st.markdown(f"**{CREATIVE_PLATFORM_LABELS.get(platform, platform.value)}**")
        candidate = package.final_content[platform]
        score_values = [
            score.overall
            for score in package.scores
            if score.candidate_id == candidate.candidate_id
        ]
        with st.expander("Post Preview / Evidence", expanded=True):
            st.write(candidate.content)
            with st.container(horizontal=True):
                st.metric(
                    "Creative Score",
                    f"{max(score_values):.0f}" if score_values else "未評価",
                )
                st.metric("Evidence", len(candidate.evidence_ids))
                st.metric("Claims", len(candidate.claims_used))
            st.caption(
                "Evidence IDs: "
                + (", ".join(candidate.evidence_ids) if candidate.evidence_ids else "なし")
            )
            st.caption("修正・再生成はAI制作スタジオで行い、変更後の版を改めて承認してください。")
        candidates = [item for item in accounts if item.platform is mapped]
        chosen: SocialAccountConnection | None = None
        if candidates:
            chosen = st.selectbox(
                "Target Account",
                candidates,
                format_func=_account_label,
                key=f"approval_account_{package_id}_{platform.value}",
            )
            account_selection[platform] = chosen.connection_id
        else:
            st.warning(f"{PLATFORM_LABELS[mapped]}のTarget Account台帳がありません。")
        asset_options = _platform_assets(package_id, platform)
        asset_selection[platform] = st.multiselect(
            "承認対象Asset",
            asset_options,
            key=f"approval_assets_{package_id}_{platform.value}",
            help="実ファイルだけが候補になります。Media投稿では対象Assetを明示選択してください。",
        )
        if platform is CreativePlatform.TIKTOK:
            account_metadata = chosen.metadata if chosen else {}
            raw_options = account_metadata.get("privacy_level_options", [])
            privacy_options = (
                [str(value) for value in raw_options]
                if isinstance(raw_options, list) and raw_options
                else ["SELF_ONLY"]
            )
            if not account_metadata.get("client_audited", False):
                privacy_options = [
                    value for value in privacy_options if value == "SELF_ONLY"
                ] or ["SELF_ONLY"]
                st.info("未監査TikTok Clientのため公開範囲はSELF_ONLYに固定されます。")
            if not account_metadata.get("creator_info_checked_at"):
                st.warning("接続タブでTikTok Creator Infoを更新してから承認してください。")
            privacy = st.selectbox(
                "TikTok privacy",
                privacy_options,
                key=f"approval_privacy_{package_id}_{platform.value}",
            )
            comment_locked = bool(account_metadata.get("comment_disabled", False))
            duet_locked = bool(account_metadata.get("duet_disabled", False))
            stitch_locked = bool(account_metadata.get("stitch_disabled", False))
            disable_comment = st.checkbox(
                "コメントを無効化",
                value=comment_locked,
                disabled=comment_locked,
                key=f"approval_tiktok_comment_{package_id}",
            )
            disable_duet = st.checkbox(
                "デュエットを無効化",
                value=duet_locked,
                disabled=duet_locked,
                key=f"approval_tiktok_duet_{package_id}",
            )
            disable_stitch = st.checkbox(
                "リミックスを無効化",
                value=stitch_locked,
                disabled=stitch_locked,
                key=f"approval_tiktok_stitch_{package_id}",
            )
            commercial = st.checkbox(
                "商品・ブランドを宣伝する商用コンテンツ",
                key=f"approval_tiktok_commercial_{package_id}",
            )
            brand_content = st.checkbox(
                "Paid partnership（第三者ブランド）",
                disabled=not commercial,
                key=f"approval_tiktok_brand_content_{package_id}",
            )
            brand_organic = st.checkbox(
                "Your brand（自社・自分のブランド）",
                disabled=not commercial,
                key=f"approval_tiktok_brand_organic_{package_id}",
            )
            st.caption("AI生成コンテンツとしてTikTokへ申告します。")
            consent = st.checkbox(
                "この動画・本文・公開設定をTikTokへ送信することに同意します。",
                key=f"approval_tiktok_consent_{package_id}",
            )
            metadata[platform] = {
                "privacy_level": privacy,
                "disable_comment": disable_comment,
                "disable_duet": disable_duet,
                "disable_stitch": disable_stitch,
                "commercial_content": commercial,
                "brand_content_toggle": brand_content if commercial else False,
                "brand_organic_toggle": brand_organic if commercial else False,
                "tiktok_consent_confirmed": consent,
            }
        elif platform in {
            CreativePlatform.YOUTUBE_LONG,
            CreativePlatform.YOUTUBE_SHORTS,
        }:
            privacy = st.selectbox(
                "YouTube privacy",
                ["private", "unlisted", "public"],
                key=f"approval_privacy_{package_id}_{platform.value}",
            )
            metadata[platform] = {"privacy": privacy, "made_for_kids": False}
        elif platform is CreativePlatform.PINTEREST:
            board_id = st.text_input(
                "Pinterest board ID",
                key=f"approval_board_{package_id}_{platform.value}",
            )
            metadata[platform] = {"board_id": board_id}
        elif platform is CreativePlatform.INSTAGRAM_FEED:
            image_url = st.text_input(
                "公開HTTPS JPEG URL",
                key=f"approval_image_url_{package_id}_{platform.value}",
                placeholder="https://cdn.example.com/approved-image.jpg",
                help=(
                    "Metaがログインなしで取得できるJPEG URLです。URLは本文・Assetと一緒に"
                    "承認スナップショットへ固定されます。"
                ),
            )
            metadata[platform] = {"media_type": "IMAGE", "image_url": image_url}

    reviewer = st.text_input("承認者", key="publishing_approval_reviewer")
    confirmed = st.checkbox(
        "本文・Asset・Evidence・Target Accountを確認し、この版を公開対象としてロックします。",
        key=f"publishing_approval_confirmed_{package_id}",
    )
    if st.button(
        "Approve All & lock" if approve_all else "Approve Selected & lock",
        type="primary",
        icon=":material/lock:",
        disabled=not selected or not confirmed,
    ):
        try:
            missing_accounts = [item.value for item in selected if item not in account_selection]
            if missing_accounts:
                raise ValueError("Target Account未選択: " + ", ".join(missing_accounts))
            with session_scope() as session:
                approval, snapshots = PublishingApprovalService().approve_and_lock(
                    session,
                    package_id,
                    selected,
                    reviewer,
                    account_selection,
                    platform_assets=asset_selection,
                    platform_metadata=metadata,
                )
            st.success(
                f"Approval {approval.approval_id} を作成し、{len(snapshots)}件をロックしました。"
            )
            st.rerun()
        except Exception as exc:
            _show_error(exc)

    with st.expander("Revisionを依頼"):
        st.caption("Active Approvalを無効化し、理由を監査Logへ保存します。")
        revision_reason = st.selectbox(
            "Revision reason",
            [
                "too_generic",
                "too_salesy",
                "weak_hook",
                "wrong_visual",
                "fact_issue",
                "brand_mismatch",
                "other",
            ],
            key=f"publishing_revision_reason_{package_id}",
        )
        revision_feedback = st.text_area(
            "修正指示",
            key=f"publishing_revision_feedback_{package_id}",
        )
        revision_requester = st.text_input(
            "Revision依頼者",
            key=f"publishing_revision_requester_{package_id}",
        )
        if st.button(
            "Request revision",
            icon=":material/rate_review:",
            key=f"publishing_request_revision_{package_id}",
        ):
            try:
                with session_scope() as session:
                    invalidated = PublishingApprovalService().request_revision(
                        session,
                        package_id,
                        revision_requester,
                        revision_reason,
                        revision_feedback,
                    )
                st.success(f"{len(invalidated)}件のApprovalを無効化しました。")
                st.rerun()
            except Exception as exc:
                _show_error(exc)

    st.subheader("Approval履歴")
    if approvals:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Approval": item.approval_id,
                        "Package": item.content_package_id,
                        "Version": item.content_version,
                        "Platforms": ", ".join(p.value for p in item.approved_platforms),
                        "承認者": item.approved_by,
                        "状態": item.status.value,
                        "Hash": item.content_hash[:12],
                        "承認日時": item.approved_at,
                    }
                    for item in approvals
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        invalid = sum(item.status is ApprovalRecordStatus.INVALIDATED for item in approvals)
        if invalid:
            st.caption(f"変更検知により無効化されたApproval: {invalid}件")


def _scheduling_tab() -> None:
    settings = get_settings()
    with session_scope() as session:
        snapshots = list_snapshots(session)
        schedules = list_schedules(session)
    st.subheader("Schedule Approval")
    st.caption("AI推奨時刻は提案に留まり、保存前に人が日時・Timezoneを承認します。")
    if not snapshots:
        st.info("先にContent PackageをHuman approval & lockしてください。")
        return

    snapshot_map = {
        item.snapshot_id: (
            f"{PLATFORM_LABELS[item.platform]} · {item.content_package_id} · "
            f"v{item.version} · {item.snapshot_id}"
        )
        for item in snapshots
    }
    snapshot_id = st.selectbox(
        "Approved Snapshot",
        list(snapshot_map),
        format_func=lambda item: snapshot_map[item],
    )
    timezone_name = st.text_input(
        "Timezone",
        value=settings.publishing_default_timezone,
        key="publishing_schedule_timezone",
    )
    scheduled_date = st.date_input("公開日", key="publishing_schedule_date")
    scheduled_time = st.time_input(
        "公開時刻",
        value=time(hour=9),
        key="publishing_schedule_time",
    )
    approver = st.text_input("Schedule承認者", key="publishing_schedule_approver")
    confirmed = st.checkbox(
        "Target Account・公開日時・Timezoneを確認しました。",
        key="publishing_schedule_confirmed",
    )
    if st.button(
        "Scheduleを承認・Queueへ追加",
        type="primary",
        icon=":material/event_available:",
        disabled=not confirmed,
    ):
        try:
            scheduled_at = datetime.combine(
                scheduled_date,
                scheduled_time,
                tzinfo=ZoneInfo(timezone_name),
            )
            with session_scope() as session:
                schedule, job = PublishingScheduler(settings).create_approved_schedule(
                    session,
                    snapshot_id,
                    scheduled_at,
                    timezone_name,
                    approver,
                )
            st.success(f"Schedule {schedule.schedule_id} / Job {job.job_id} を作成しました。")
            st.rerun()
        except Exception as exc:
            _show_error(exc)

    selected_snapshot = next(item for item in snapshots if item.snapshot_id == snapshot_id)
    try:
        recommendations = PublishingScheduler(settings).recommend(
            [selected_snapshot.platform], timezone=timezone_name
        )
    except Exception:
        recommendations = []
    if recommendations:
        st.caption(
            "AI提案: "
            + " / ".join(
                f"{item.recommended_at.strftime('%Y-%m-%d %H:%M %Z')} ({item.reason})"
                for item in recommendations
            )
        )

    st.subheader("Schedule一覧")
    if schedules:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Schedule": item.schedule_id,
                        "Platform": PLATFORM_LABELS[item.platform],
                        "Target": item.target_account_id,
                        "日時": item.scheduled_at,
                        "Timezone": item.timezone,
                        "状態": item.status.value,
                        "承認者": item.approved_by,
                    }
                    for item in schedules
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.subheader("Reschedule")
        schedule_map = {
            item.schedule_id: (
                f"{PLATFORM_LABELS[item.platform]} · "
                f"{item.scheduled_at.strftime('%Y-%m-%d %H:%M')} · {item.status.value}"
            )
            for item in schedules
        }
        reschedule_id = st.selectbox(
            "変更するSchedule",
            list(schedule_map),
            format_func=lambda item: schedule_map[item],
        )
        reschedule_date = st.date_input("新しい公開日", key="publishing_reschedule_date")
        reschedule_time = st.time_input(
            "新しい公開時刻",
            value=time(hour=9),
            key="publishing_reschedule_time",
        )
        reschedule_timezone = st.text_input(
            "新しいTimezone",
            value=settings.publishing_default_timezone,
            key="publishing_reschedule_timezone",
        )
        reschedule_approver = st.text_input(
            "Reschedule承認者",
            key="publishing_reschedule_approver",
        )
        reschedule_confirmed = st.checkbox(
            "旧ScheduleをCancelし、新しい日時を承認します。",
            key="publishing_reschedule_confirmed",
        )
        if st.button(
            "Reschedule",
            icon=":material/update:",
            disabled=not reschedule_confirmed,
        ):
            try:
                new_scheduled_at = datetime.combine(
                    reschedule_date,
                    reschedule_time,
                    tzinfo=ZoneInfo(reschedule_timezone),
                )
                with session_scope() as session:
                    PublishingScheduler(settings).reschedule(
                        session,
                        reschedule_id,
                        new_scheduled_at,
                        reschedule_timezone,
                        reschedule_approver,
                    )
                st.success("Scheduleを変更しました。")
                st.rerun()
            except Exception as exc:
                _show_error(exc)


def _queue_tab() -> None:
    settings = get_settings()
    registry = build_publisher_registry(settings)
    with session_scope() as session:
        jobs = list_jobs(session)
    st.subheader("Publication Queue")
    st.caption(
        "Due Jobだけを実行します。Dry Runでは外部Publicationを作りません。"
        "本番通信の結果が不明な場合は自動再送せず、担当者の照合を待ちます。"
    )
    if not jobs:
        st.info("Queueは空です。")
        return

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Job": item.job_id,
                    "Platform": PLATFORM_LABELS[item.platform],
                    "予定": item.scheduled_at,
                    "状態": item.status.value,
                    "試行": f"{item.attempt_count}/{item.max_attempts}",
                    "次回": item.next_attempt_at,
                    "Error": item.error,
                }
                for item in jobs
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    selected_job_id = st.selectbox("操作するJob", [item.job_id for item in jobs])
    selected_job = next(item for item in jobs if item.job_id == selected_job_id)
    with session_scope() as session:
        uncertain_publication = get_publication_by_key(
            session, selected_job.idempotency_key
        )
    actor = st.text_input("Queue操作担当者", key="publishing_queue_actor")
    with st.container(horizontal=True):
        if st.button(
            "Cancel",
            icon=":material/cancel:",
            disabled=selected_job.status in TERMINAL_JOB_STATUSES,
        ):
            try:
                with session_scope() as session:
                    PublishingScheduler(settings).cancel(session, selected_job.schedule_id, actor)
                st.rerun()
            except Exception as exc:
                _show_error(exc)
        if st.button(
            "Pause job",
            icon=":material/pause:",
            disabled=selected_job.status is not PublishingStatus.QUEUED,
        ):
            try:
                with session_scope() as session:
                    PublishingScheduler(settings).pause(session, selected_job.schedule_id)
                st.rerun()
            except Exception as exc:
                _show_error(exc)
        if st.button(
            "Resume job",
            icon=":material/play_arrow:",
            disabled=selected_job.status is not PublishingStatus.WAITING,
        ):
            try:
                with session_scope() as session:
                    PublishingScheduler(settings).resume(session, selected_job.schedule_id)
                st.rerun()
            except Exception as exc:
                _show_error(exc)

    stale_after_seconds = max(
        settings.publishing_worker_lease_seconds,
        settings.publishing_worker_stale_seconds,
    )
    publishing_age_seconds = (
        datetime.now(UTC) - selected_job.updated_at.astimezone(UTC)
    ).total_seconds()
    can_reconcile = (
        selected_job.status is PublishingStatus.RECONCILIATION_REQUIRED
        or (
            selected_job.status is PublishingStatus.PUBLISHING
            and publishing_age_seconds >= stale_after_seconds
        )
    )
    if selected_job.status is PublishingStatus.PUBLISHING and not can_reconcile:
        st.info(
            "このJobは現在投稿処理中です。Worker leaseの猶予時間を過ぎても完了しない場合、"
            "結果照合の対象になります。"
        )
    if can_reconcile and selected_job.status in RECONCILIABLE_STATUSES:
        with st.container(border=True):
            st.subheader("外部投稿結果の照合", anchor=False)
            st.error(
                "外部サービスでは投稿が成功している可能性があります。確認が終わるまで、"
                "このJobは自動再送されません。",
                icon=":material/report:",
            )
            outcome = st.radio(
                "外部サービスで確認した結果",
                ["投稿済み", "未投稿"],
                horizontal=True,
                key=f"publishing_reconciliation_outcome_{selected_job.job_id}",
            )
            remote_post_id = ""
            remote_url = ""
            if outcome == "投稿済み":
                remote_post_id = st.text_input(
                    "外部サービスの投稿ID",
                    value=(
                        uncertain_publication.remote_post_id
                        if uncertain_publication
                        else ""
                    ),
                    key=f"publishing_reconciliation_remote_id_{selected_job.job_id}",
                    help="投稿URLだけでなく、外部サービスに表示される投稿IDを入力します。",
                )
                remote_url = st.text_input(
                    "確認した投稿URL（任意）",
                    value=(
                        uncertain_publication.remote_url or ""
                        if uncertain_publication
                        else ""
                    ),
                    key=f"publishing_reconciliation_remote_url_{selected_job.job_id}",
                    placeholder="https://...",
                )
                confirmation_label = (
                    "外部サービス上で対象投稿を確認し、このJobを投稿済みに確定します。"
                )
            else:
                confirmation_label = (
                    "外部サービス上に対象投稿がないことを確認しました。"
                    "同じ承認内容を再キューしてよいことを理解しています。"
                )
            reconciled = st.checkbox(
                confirmation_label,
                key=f"publishing_reconciliation_confirmed_{selected_job.job_id}_{outcome}",
            )
            if st.button(
                "照合結果を保存",
                type="primary",
                icon=":material/fact_check:",
                disabled=(
                    not actor.strip()
                    or not reconciled
                    or (outcome == "投稿済み" and not remote_post_id.strip())
                ),
                key=f"publishing_reconciliation_save_{selected_job.job_id}",
            ):
                try:
                    with session_scope() as session:
                        resolve_outcome(
                            session,
                            selected_job.job_id,
                            actor=actor,
                            published=outcome == "投稿済み",
                            provider=registry.get(selected_job.platform).key,
                            remote_post_id=remote_post_id,
                            remote_url=remote_url,
                        )
                    st.success("照合結果と監査ログを保存しました。")
                    st.rerun()
                except Exception as exc:
                    _show_error(exc)

    if st.button(
        "Due Jobを安全実行",
        type="primary",
        icon=":material/science:",
        help="本番ゲートがOFFの間はDry Run payload生成だけを行います。",
    ):
        try:
            result = asyncio.run(
                PublishingWorker(
                    settings,
                    registry,
                    SessionLocal,
                ).run_once("streamlit-one-shot")
            )
            if result.results:
                st.success(f"{len(result.results)}件を処理しました。")
            else:
                st.info("実行時刻に達したJobはありません。")
            st.rerun()
        except Exception as exc:
            _show_error(exc)

    if selected_job.preflight:
        st.subheader("最新Preflight")
        st.json(selected_job.preflight)


def _performance_tab() -> None:
    with session_scope() as session:
        publications = list_publications(session)
        snapshots = list_performance_snapshots(session)
        learnings = list_learning(session)
        patterns = list_winning_patterns(session)
        creative_performance = list_creative_performance(session)

    st.subheader("Performance & Learning")
    if not publications:
        st.info(
            "Dry Runは外部Publicationを作らないため、実Performanceはまだありません。"
            "本番移行後に公式APIから取得します。"
        )
    else:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Publication": item.publication_id,
                        "Platform": PLATFORM_LABELS[item.platform],
                        "Remote post ID": item.remote_post_id,
                        "状態": item.status.value,
                        "公開日時": item.published_at,
                    }
                    for item in publications
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    if snapshots:
        rows = []
        for item in snapshots:
            normalized = PerformanceNormalizer.normalize(item)
            rows.append(
                {
                    "Platform": PLATFORM_LABELS[item.platform],
                    "Window": item.window,
                    "Impressions": item.impressions,
                    "Views": item.views,
                    "Engagement rate": normalized.engagement_rate,
                    "CTR": normalized.ctr,
                    "Completion": normalized.completion_rate,
                    "Measured": item.measured_at,
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("Learning Records")
    if learnings:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Market": item.market,
                        "Platform": item.platform.value if item.platform else "all",
                        "Observation": item.observation,
                        "Interpretation / hypothesis": item.interpretation,
                        "Recommendation": item.recommendation,
                        "Confidence": item.confidence,
                    }
                    for item in learnings
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Observationと解釈を分離したLearningはまだありません。")

    st.subheader("Winning Patterns")
    if patterns:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Market": item.market,
                        "Platform": PLATFORM_LABELS[item.platform],
                        "Hook": item.hook_type,
                        "CTA": item.cta_type,
                        "Sample size": item.sample_size,
                        "Percentile": item.percentile,
                    }
                    for item in patterns
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("High performerが3件以上揃うまでWinning Patternへ昇格しません。")

    st.subheader("Model / Provider Performance")
    if creative_performance:
        grouped: dict[
            tuple[PublishingPlatform, str, str],
            list[CreativePerformanceRecord],
        ] = {}
        for record in creative_performance:
            grouped.setdefault((record.platform, record.provider, record.model), []).append(record)
        model_rows = []
        for (platform, provider, model), records in grouped.items():
            judge_scores = [item.judge_score for item in records if item.judge_score is not None]
            ctr_values = [
                float(value)
                for record in records
                if isinstance(
                    (value := record.performance_metrics.get("ctr")),
                    (int, float),
                )
            ]
            model_rows.append(
                {
                    "Platform": PLATFORM_LABELS[platform],
                    "Provider": provider,
                    "Model": model,
                    "Published": len(records),
                    "Average judge": fmean(judge_scores) if judge_scores else None,
                    "High performer rate": (
                        sum(item.high_performer for item in records) / len(records)
                    ),
                    "Average real CTR": fmean(ctr_values) if ctr_values else None,
                }
            )
        st.dataframe(pd.DataFrame(model_rows), hide_index=True, width="stretch")
        st.caption("Platformごとに集計し、異なるSNSのMetricを単純合算していません。")
    else:
        st.caption("Published Contentと実Performanceが結合されるとModel別に表示します。")


def _capabilities_tab() -> None:
    settings = get_settings()
    registry = build_publisher_registry(settings)
    with session_scope() as session:
        accounts = list_accounts(session)
        audits = list_audits(session, limit=100)
    account_by_platform = {item.platform: item for item in accounts}
    capabilities = asyncio.run(registry.capabilities(account_by_platform))

    st.subheader("Publisher Capability Matrix")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Platform": PLATFORM_LABELS[platform],
                    "Provider": capability.provider,
                    "Availability": capability.availability.value,
                    "Enabled": capability.enabled,
                    "Operations": ", ".join(capability.operations),
                    "Content": ", ".join(capability.content_types),
                    "Required scopes": ", ".join(capability.required_scopes),
                    "Message": capability.message,
                }
                for platform, capability in capabilities.items()
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "X / Instagram / TikTok / YouTube / Pinterest / Reddit / Genericを分離実装済み。"
        "Production live adapterはXテキスト、Instagram単一JPEG画像、Pinterest画像Pinの3系統です。"
        "初期ゲートはOFFです。"
    )

    st.subheader("Audit Log")
    if audits:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "日時": item.created_at,
                        "Event": item.event_type.value,
                        "Actor": item.actor,
                        "Platform": item.platform.value if item.platform else "",
                        "状態": item.status,
                        "Retry": item.retry_count,
                        "Error": item.error,
                    }
                    for item in audits
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("Audit Logはまだありません。")


def _worker_notifications_tab() -> None:
    settings = get_settings()
    with session_scope() as session:
        heartbeats = list_worker_heartbeats(session)
        leases = list_worker_leases(session)
        notifications = list_notifications(session)

    st.subheader("Publishing Worker")
    st.caption(
        "WorkerはDB Leaseで同じJobの同時取得を防ぎます。"
        "この画面から常駐Processを起動することはありません。"
    )
    now = datetime.now(UTC)
    active_workers = [
        item
        for item in heartbeats
        if (now - item.last_seen_at).total_seconds() <= settings.publishing_worker_stale_seconds
    ]
    with st.container(horizontal=True):
        st.metric("Active workers", len(active_workers), border=True)
        st.metric("Current leases", len(leases), border=True)
        st.metric(
            "Pending notifications",
            sum(item.status is NotificationStatus.PENDING for item in notifications),
            border=True,
        )

    if heartbeats:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Worker": item.worker_id,
                        "State": item.state.value,
                        "Last seen": item.last_seen_at,
                        "Active": item in active_workers,
                        "Processed": item.processed_count,
                        "Failed": item.failed_count,
                        "Current job": item.current_job_id,
                        "Message": item.message,
                    }
                    for item in heartbeats
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("Worker heartbeatはまだありません。")

    if leases:
        st.markdown("**Job leases**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Job": item.job_id,
                        "Worker": item.worker_id,
                        "Acquired": item.acquired_at,
                        "Lease until": item.lease_until,
                    }
                    for item in leases
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    if st.button(
        "Workerを1バッチ実行",
        icon=":material/play_arrow:",
        help="現在の安全ゲートを維持したままDue Jobだけを処理します。",
    ):
        try:
            result = asyncio.run(
                PublishingWorker(
                    settings,
                    build_publisher_registry(settings),
                    SessionLocal,
                ).run_once("streamlit-one-shot")
            )
            if result.skipped_reason:
                st.warning(result.skipped_reason)
            else:
                st.success(
                    f"Claimed {len(result.claimed_job_ids)} / Processed {len(result.results)}"
                )
            st.rerun()
        except Exception as exc:
            _show_error(exc)

    st.code(
        ".venv\\Scripts\\python.exe -m app.publishing.worker_cli --once",
        language="powershell",
    )
    st.caption(
        "常駐化する場合は`--once`を外します。本番Service登録はOAuth/Sandbox試験後に行います。"
    )

    st.subheader("Notification outbox")
    st.caption("現在のTargetはin_appだけです。秘密情報は通知Payloadへ含めません。")
    if notifications:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Notification": item.notification_id,
                        "Created": item.created_at,
                        "Severity": item.severity.value,
                        "Event": item.event_type,
                        "Title": item.title,
                        "Message": item.message,
                        "Status": item.status.value,
                        "Job": item.job_id,
                    }
                    for item in notifications
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        pending = [item for item in notifications if item.status is NotificationStatus.PENDING]
        if pending:
            notification_id = st.selectbox(
                "確認する通知",
                [item.notification_id for item in pending],
                format_func=lambda value: next(
                    item.title for item in pending if item.notification_id == value
                ),
            )
            if st.button("確認済みにする", icon=":material/done_all:"):
                try:
                    with session_scope() as session:
                        acknowledge_notification(session, notification_id)
                    st.rerun()
                except Exception as exc:
                    _show_error(exc)
    else:
        st.info("通知はまだありません。")


_handle_oauth_callback()
_render_oauth_notice()
_status_header()

tabs = st.tabs(
    [
        "概要・停止",
        "Accounts",
        "Approval",
        "Schedule",
        "Queue",
        "Performance",
        "Capabilities・Audit",
        "Worker・通知",
    ],
    on_change="rerun",
)
if tabs[0].open:
    with tabs[0]:
        _overview_tab()
if tabs[1].open:
    with tabs[1]:
        _accounts_tab()
if tabs[2].open:
    with tabs[2]:
        _approval_tab()
if tabs[3].open:
    with tabs[3]:
        _scheduling_tab()
if tabs[4].open:
    with tabs[4]:
        _queue_tab()
if tabs[5].open:
    with tabs[5]:
        _performance_tab()
if tabs[6].open:
    with tabs[6]:
        _capabilities_tab()
if tabs[7].open:
    with tabs[7]:
        _worker_notifications_tab()
