from unittest.mock import MagicMock, call, patch

from app.streamlit_support import NOTE_NEW_POST_URL, show_note_posting_assistant


@patch("app.streamlit_support.st")
def test_note_posting_assistant_shows_copy_fields_and_official_editor(
    mock_st: MagicMock,
) -> None:
    show_note_posting_assistant(
        title="  テストタイトル  ",
        body="  ## 見出し\n本文  ",
        enabled=True,
        key="note_test",
    )

    mock_st.code.assert_has_calls(
        [
            call("テストタイトル", language=None, wrap_lines=True),
            call("## 見出し\n本文", language=None, wrap_lines=True, height=320),
        ]
    )
    mock_st.link_button.assert_called_once_with(
        "3. note投稿画面を開く",
        NOTE_NEW_POST_URL,
        key="note_test",
        icon=":material/open_in_new:",
        type="primary",
        disabled=False,
        width="stretch",
    )


@patch("app.streamlit_support.st")
def test_note_posting_assistant_disables_editor_until_reviewed(mock_st: MagicMock) -> None:
    show_note_posting_assistant(title="題名", body="本文", enabled=False, key="note_disabled")

    assert mock_st.link_button.call_args.kwargs["disabled"] is True
    mock_st.caption.assert_called_with("公開前チェックを確認し、確認欄にチェックすると開けます。")
