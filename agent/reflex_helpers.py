from typing import Any, Dict, Optional

from tools.file_inspector import inspect_sqlite_table


def message_choice(content: str) -> Dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": str(content or "").strip(),
                }
            }
        ]
    }


def handle_directory_scan_reflex(core: Any, tool_name: str, tool_args: Dict[str, Any], user_input: str) -> Optional[Dict[str, Any]]:
    if tool_name == "__inspect_directory_tree__":
        inspect_path = str(tool_args.get("path") or "").strip()
        inspect_mode = str(tool_args.get("mode") or "all").strip().lower()
        result = core._inspect_directory_tree(inspect_path, mode=inspect_mode)
        if not result.get("ok"):
            return message_choice(f"Directory inspection failed: {result.get('error')}")
        return message_choice(str((result.get("result") or {}).get("report") or "No inspection report generated."))

    if tool_name == "__queue_directory_scan__":
        inspect_path = str(tool_args.get("path") or "").strip()
        inspect_mode = str(tool_args.get("mode") or "all").strip().lower()
        core._set_pending_directory_scan(path=inspect_path, mode=inspect_mode, user_input=user_input)
        return message_choice(
            f"I can do this reliably, but not as one giant blind read. "
            f"I will scan {inspect_path} using mode={inspect_mode} and report verified findings. "
            f"Say: 'yes scan now' to proceed, or 'cancel scan' to stop."
        )

    if tool_name == "__cancel_directory_scan__":
        pending = core._get_pending_directory_scan()
        core._clear_pending_directory_scan()
        target = str((pending or {}).get("path") or "the pending directory scan")
        return message_choice(f"Canceled. I will not scan {target}.")

    if tool_name == "__confirm_directory_scan__":
        pending = core._get_pending_directory_scan()
        if not pending:
            return message_choice("No pending directory scan to run.")
        inspect_path = str(pending.get("path") or "").strip()
        inspect_mode = str(pending.get("mode") or "all").strip().lower()
        result = core._inspect_directory_tree(inspect_path, mode=inspect_mode)
        core._clear_pending_directory_scan()
        if not result.get("ok"):
            return message_choice(f"Directory inspection failed: {result.get('error')}")
        return message_choice(str((result.get("result") or {}).get("report") or "No inspection report generated."))

    return None


def handle_external_write_reflex(core: Any, tool_name: str) -> Optional[Dict[str, Any]]:
    if tool_name == "__cancel_external_write__":
        pending = core._get_pending_write_authorization()
        core._clear_pending_write_authorization()
        if pending:
            target = str(pending.get("target") or "the pending path")
            return message_choice(f"Canceled. I will not write outside E:\\workspace for {target}.")
        return message_choice("No pending external write request to cancel.")

    if tool_name == "__confirm_external_write__":
        pending = core._get_pending_write_authorization()
        if not pending:
            return message_choice("No pending external write request to authorize.")

        pending_tool = str(pending.get("tool_name") or "").strip()
        pending_args = dict(pending.get("tool_args") or {})
        pending_args["authorized_write"] = True

        result = core.tools.run(pending_tool, pending_args)
        formatted = core._format_tool_result(pending_tool, result)

        if bool(result.get("ok", False)):
            core._clear_pending_write_authorization()
        else:
            err = str(result.get("error") or "")
            if "write_auth_token_invalid" in err:
                formatted = (
                    "Authorization acknowledged, but a write token is required and did not match. "
                    "Set MK1_WRITE_AUTH_TOKEN in the environment and retry."
                )
            else:
                core._clear_pending_write_authorization()

        return message_choice(formatted)

    return None


def handle_identity_reflex(core: Any, tool_name: str, tool_args: Dict[str, Any], request_context: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if tool_name == "__mina_identity__":
        return message_choice("I am Mina, your chaotic gremlin-fox assistant.")

    if tool_name == "__voice_auth_status__":
        enrolled = bool(core._voice_passphrase_hash)
        trusted_cfg = bool(core.trusted_client_tokens)
        trusted_client = core._is_trusted_client(request_context)
        recently_verified = core._is_client_recently_verified(request_context)
        return message_choice(
            f"Voice passphrase enrolled={enrolled}. "
            f"Trusted-client tokens configured={trusted_cfg}. "
            f"This client trusted={trusted_client}. "
            f"This client recently voice-verified={recently_verified}."
        )

    if tool_name == "__voice_passphrase_enroll__":
        enroll = core._enroll_voice_passphrase(str(tool_args.get("phrase") or ""))
        if not enroll.get("ok"):
            return message_choice("Voice passphrase enroll failed. Use a longer phrase.")
        hint = str((enroll.get("result") or {}).get("hint") or "")
        return message_choice(f"Voice passphrase enrolled. Hint: {hint}.")

    if tool_name == "__voice_identity_verify__":
        phrase = str(tool_args.get("phrase") or "").strip()
        if core._is_voice_phrase_match(phrase):
            core._mark_client_verified(request_context)
            return message_choice("Voice verified for this client. Sensitive actions are unlocked for a short window.")
        return message_choice("Voice verification failed. Please try the exact enrolled phrase.")

    return None


def handle_direct_response_reflex(core: Any, tool_name: str, tool_args: Dict[str, Any], user_input: str) -> Optional[Dict[str, Any]]:
    if tool_name == "__spotify_dj_capability__":
        return message_choice(
            "Yes. I can do that. I can read track duration and auto-switch songs from your liked list when each song ends. "
            "Say: 'start dj mode from my favorite list'. You can stop it with: 'stop dj mode'."
        )

    if tool_name == "__tool_self_test__":
        result = core._run_tool_self_test(str(tool_args.get("request") or user_input))
        formatted = core._format_tool_result(tool_name, result)
        return message_choice(formatted)

    if tool_name == "__inspect_sqlite_table__":
        inspect_path = str(tool_args.get("path") or "").strip()
        table_name = str(tool_args.get("table") or "").strip()
        row_limit = int(tool_args.get("row_limit") or 8)
        result = inspect_sqlite_table(inspect_path, table_name=table_name, row_limit=row_limit)
        if not result.get("ok"):
            return message_choice(f"SQLite table preview failed: {result.get('error')}")
        content = str((result.get("result") or {}).get("content") or "No SQLite preview generated.")
        return message_choice(content)

    if tool_name == "__clarify_intent__":
        question = str(tool_args.get("question") or "Please clarify your intent.").strip()
        return message_choice(question)

    if tool_name == "__capability_limit__":
        limitation = str(tool_args.get("message") or "I cannot perform that external action directly.").strip()
        return message_choice(limitation)

    return None
