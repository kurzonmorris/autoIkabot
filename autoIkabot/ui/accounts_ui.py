"""Account selection UI (Phase 1.5).

Flow:
1. Mode selection: stored (master password) vs manual (nothing stored)
2. Stored mode: decrypt accounts file, display list, select/add/remove
3. Manual mode: prompt for email + password directly
4. Confirmation screen before proceeding to login

Returns a dict with everything needed for Phase 2 (login).
"""

from typing import Any, Dict, List, Optional

from autoIkabot.config import ACCOUNTS_FILE
from autoIkabot.data.account_store import (
    accounts_file_exists,
    load_accounts,
    save_accounts,
    add_account,
    remove_account,
    edit_account,
    list_accounts_summary,
    Account,
)
from autoIkabot.utils.crypto import get_master_password_from_environment
from autoIkabot.utils.logging import get_logger
from autoIkabot.ui.prompts import (
    read_input,
    read_password,
    read_choice,
    read_yes_no,
    clear_screen,
)

logger = get_logger(__name__)

# Warning shown on first-time setup with master password
_FIRST_TIME_WARNING = """
========================================================
  IMPORTANT: If you forget the master password, ALL
  saved accounts are permanently lost. There is no
  recovery mechanism. This is by design for security.
========================================================
"""

# Warning shown when user selects manual mode
_MANUAL_MODE_WARNING = (
    "\nNote: In manual mode, credentials exist only in memory.\n"
    "Background tasks that require re-login will NOT work\n"
    "because credentials are not persisted to disk.\n"
)


def _prompt_master_password(confirm_new: bool = False) -> str:
    """Get the master password from environment or interactive prompt.

    Checks Docker secrets and env var first (for headless/Docker setups).
    Falls back to interactive prompt.

    Parameters
    ----------
    confirm_new : bool
        If True, ask the user to confirm the password (for first-time setup).

    Returns
    -------
    str
        The master password.
    """
    # Try non-interactive sources first (Docker secrets, env var)
    env_password = get_master_password_from_environment()
    if env_password is not None:
        logger.info("Master password obtained from environment/Docker secret.")
        return env_password

    # Interactive prompt
    while True:
        password = read_password("Master password: ")
        if not password:
            print("  Password cannot be empty.")
            continue
        if confirm_new:
            confirm = read_password("Confirm master password: ")
            if password != confirm:
                print("  Passwords do not match. Try again.")
                continue
        return password


def _display_accounts_list(accounts: List[Account]) -> None:
    """Print a numbered list of accounts (no passwords shown).

    Parameters
    ----------
    accounts : List[Account]
        The decrypted accounts list.
    """
    summaries = list_accounts_summary(accounts)
    print("\nSaved accounts:")
    for i, summary in enumerate(summaries, start=1):
        print(f"  {i}. {summary}")
    print()


def _select_server(account: Account) -> str:
    """Let user pick which server to connect to.

    If the account has only one server, auto-selects it.

    Parameters
    ----------
    account : Account
        The selected account dict.

    Returns
    -------
    str
        The chosen server string (e.g. 's59-en'), or empty string.
    """
    servers = account.get("servers", [])
    if len(servers) == 0:
        return ""
    if len(servers) == 1:
        return servers[0]

    # Multiple servers — let user choose
    print("Select server:")
    for i, srv in enumerate(servers, start=1):
        default_mark = " (default)" if srv == account.get("default_server") else ""
        print(f"  {i}. {srv}{default_mark}")

    choice = read_choice("Server number: ", min_val=1, max_val=len(servers))
    return servers[choice - 1]


def _add_new_account_flow(
    accounts: List[Account], master_password: str
) -> Optional[Dict[str, Any]]:
    """Interactive flow to add a new account and save it.

    Parameters
    ----------
    accounts : List[Account]
        Existing accounts (modified in place if account is added).
    master_password : str
        For re-saving the encrypted file after adding.

    Returns
    -------
    Optional[Dict[str, Any]]
        Account info dict for the newly added account, or None if cancelled.
    """
    print("\n--- Add New Account ---")
    email = read_input("Email: ")
    if not email:
        print("Cancelled.")
        return None

    password = read_password("Ikariam password: ")
    if not password:
        print("Cancelled.")
        return None

    server_input = read_input(
        "Server(s), comma-separated (e.g. s59-en,s12-en): "
    )
    servers = [s.strip() for s in server_input.split(",") if s.strip()]
    if not servers:
        print("  No servers specified. You can add them later via edit.")

    default_server = servers[0] if servers else ""

    print("\n  -- Gameforge Lobby Token (gf-token-production) --")
    print("  This is a session cookie that can skip the entire login flow.")
    print("  It often stays valid for months or even years.")
    print("  To get it:")
    print("    1. Log into Ikariam normally in your browser")
    print("    2. Once on your server, open the browser console (F12)")
    print("    3. If needed, type:  allow pasting")
    print("    4. Paste this command:")
    print("       document.cookie.split(';').forEach(x => {")
    print("         if (x.includes('production')) console.log(x) })")
    print("    5. Copy the UUID value after 'gf-token-production='")
    print("  Leave blank to skip (normal login will be used instead).")
    gf_token = read_input("gf-token-production (leave blank to skip): ").strip()
    # Strip the cookie name prefix if user pasted the whole thing
    if gf_token.startswith("gf-token-production="):
        gf_token = gf_token[len("gf-token-production="):]

    print("\n  -- Blackbox Token (device fingerprint) --")
    print("  This is a device fingerprint (starts with 'tra:') sent during auth.")
    print("  It typically stays the same per account for years.")
    print("  To get it manually:")
    print("    1. Open browser dev tools (F12) and go to the Network tab")
    print("    2. Log in to the Ikariam lobby (it appears as you log in,")
    print("       not when simply loading the page)")
    print("    3. Filter for 'sessions' — look for the POST request to:")
    print("       https://spark-web.gameforge.com/api/v2/authProviders/mauth/sessions")
    print("    4. In the request body/payload, find the 'blackbox' field")
    print("    5. Copy the full value (starts with 'tra:')")
    print("  If you have one, paste it now. Otherwise leave blank and")
    print("  it will be fetched automatically during login.")
    blackbox_token = read_input("Blackbox token (leave blank to skip): ")

    add_account(
        accounts, email, password, servers, default_server,
        gf_token=gf_token,
        blackbox_token=blackbox_token,
    )
    save_accounts(accounts, master_password)
    print(f"  Account '{email}' added and saved.")

    return {
        "mode": "stored",
        "email": email,
        "password": password,
        "servers": servers,
        "selected_server": default_server,
        "gf_token": gf_token,
        "blackbox_token": blackbox_token,
        "proxy": None,
        "proxy_auto": False,
    }


def _edit_account_flow(
    accounts: List[Account], index: int, master_password: str
) -> None:
    """Interactive flow to edit an existing account's fields.

    Parameters
    ----------
    accounts : List[Account]
        The accounts list (modified in place).
    index : int
        Zero-based index of the account to edit.
    master_password : str
        For re-saving the encrypted file after editing.
    """
    acct = accounts[index]
    print(f"\n--- Edit Account: {acct.get('email', '?')} ---")
    print("  Press Enter to keep the current value.\n")

    email = read_input(f"  Email [{acct.get('email', '')}]: ").strip()
    password = read_password(f"  Password [{'****' if acct.get('password') else '(empty)'}]: ")
    servers_input = read_input(
        f"  Servers [{', '.join(acct.get('servers', []))}]: "
    ).strip()
    gf_token = read_input(
        f"  gf-token [{acct.get('gf_token', '')[:8] + '...' if acct.get('gf_token') else '(empty)'}]: "
    ).strip()
    blackbox_token = read_input(
        f"  Blackbox token [{acct.get('blackbox_token', '')[:20] + '...' if acct.get('blackbox_token') else '(empty)'}]: "
    ).strip()

    # Build updates dict — only include fields the user actually changed
    updates = {}
    if email:
        updates["email"] = email
    if password:
        updates["password"] = password
    if servers_input:
        new_servers = [s.strip() for s in servers_input.split(",") if s.strip()]
        updates["servers"] = new_servers
        updates["default_server"] = new_servers[0] if new_servers else ""
    if gf_token:
        if gf_token.startswith("gf-token-production="):
            gf_token = gf_token[len("gf-token-production="):]
        updates["gf_token"] = gf_token
    if blackbox_token:
        if not blackbox_token.startswith("tra:"):
            blackbox_token = "tra:" + blackbox_token
        updates["blackbox_token"] = blackbox_token

    if not updates:
        print("  No changes made.")
        return

    edit_account(accounts, index, **updates)
    save_accounts(accounts, master_password)
    print("  Account updated and saved.")


def _delete_all_data_flow() -> bool:
    """Delete the encrypted accounts file so the user can start fresh.

    Returns
    -------
    bool
        True if the file was deleted, False if the user cancelled.
    """
    print("\n  WARNING: This will permanently delete ALL saved accounts")
    print("  and your master password. You will need to set up everything")
    print("  from scratch.")
    if not read_yes_no("Are you sure?", default=False):
        return False
    if not read_yes_no("This CANNOT be undone. Really delete everything?", default=False):
        return False

    try:
        ACCOUNTS_FILE.unlink(missing_ok=True)
        print("  All account data deleted. You can now set a new master password.")
        return True
    except OSError as e:
        logger.error("Failed to delete accounts file: %s", e)
        print(f"  ERROR: Could not delete the file: {e}")
        return False


def _stored_mode_flow() -> Optional[Dict[str, Any]]:
    """Handle the 'stored accounts' mode.

    Decrypts the accounts file, displays the list, and lets the user
    select an account, add a new one, or remove an existing one.

    Returns
    -------
    Optional[Dict[str, Any]]
        Account info dict, or None if user cancels back to mode selection.
    """
    is_first_time = not accounts_file_exists()

    if is_first_time:
        print(_FIRST_TIME_WARNING)
        master_password = _prompt_master_password(confirm_new=True)
        accounts = []
    else:
        master_password = _prompt_master_password(confirm_new=False)
        try:
            accounts = load_accounts(master_password)
        except Exception as e:
            logger.error("Failed to decrypt accounts: %s", e)
            print("\n  ERROR: Could not decrypt accounts file.")
            print("  Wrong password, or the file is corrupted.")
            print(f"  Detail: {e}")
            return None

    while True:
        if accounts:
            _display_accounts_list(accounts)
            n = len(accounts)
            print(f"  {n + 1}. Add new account")
            print(f"  {n + 2}. Edit an account")
            print(f"  {n + 3}. Remove an account")
            print(f"  {n + 4}. Delete all data & reset password")
            print(f"  0. Back to mode selection")
            print()

            choice = read_choice("Select: ", min_val=0, max_val=n + 4)

            if choice == 0:
                return None

            elif choice <= n:
                # Select an existing account
                selected = accounts[choice - 1]
                server = _select_server(selected)

                return {
                    "mode": "stored",
                    "email": selected["email"],
                    "password": selected["password"],
                    "servers": selected.get("servers", []),
                    "selected_server": server,
                    "gf_token": selected.get("gf_token", ""),
                    "blackbox_token": selected.get("blackbox_token", ""),
                    "proxy": selected.get("proxy"),
                    "proxy_auto": selected.get("proxy_auto", False),
                }

            elif choice == n + 1:
                # Add new account
                result = _add_new_account_flow(accounts, master_password)
                if result is not None:
                    return result
                continue

            elif choice == n + 2:
                # Edit an account
                edit_choice = read_choice(
                    "Account number to edit: ", min_val=1, max_val=n
                )
                _edit_account_flow(accounts, edit_choice - 1, master_password)
                continue

            elif choice == n + 3:
                # Remove an account
                rm_choice = read_choice(
                    "Account number to remove: ", min_val=1, max_val=n
                )
                acct = accounts[rm_choice - 1]
                if read_yes_no(
                    f"Remove '{acct.get('email', '?')}'? This cannot be undone.",
                    default=False,
                ):
                    remove_account(accounts, rm_choice - 1)
                    save_accounts(accounts, master_password)
                    print("  Account removed.")
                continue

            elif choice == n + 4:
                # Delete all data & reset password
                if _delete_all_data_flow():
                    # File deleted — break out so _stored_mode_flow restarts
                    # from the caller's while loop (run_account_selection)
                    return None
                continue
        else:
            # No accounts yet — prompt to add one
            print("\n  No saved accounts. Let's add one.")
            result = _add_new_account_flow(accounts, master_password)
            if result is not None:
                return result
            return None


def _manual_mode_flow() -> Optional[Dict[str, Any]]:
    """Handle the 'manual entry' mode (nothing stored to disk).

    Returns
    -------
    Optional[Dict[str, Any]]
        Account info dict, or None if cancelled.
    """
    print(_MANUAL_MODE_WARNING)

    email = read_input("Email: ")
    if not email:
        return None

    password = read_password("Password: ")
    if not password:
        return None

    server = read_input("Server (e.g. s59-en, or leave blank to auto-detect): ")

    return {
        "mode": "manual",
        "email": email,
        "password": password,
        "servers": [server] if server else [],
        "selected_server": server,
        "gf_token": "",
        "blackbox_token": "",
        "proxy": None,
        "proxy_auto": False,
    }


def _display_confirmation(info: Dict[str, Any]) -> bool:
    """Display confirmation screen before proceeding to login.

    Parameters
    ----------
    info : Dict[str, Any]
        The account info dict.

    Returns
    -------
    bool
        True if user confirms, False to go back.
    """
    print("\n" + "=" * 48)
    print("  Confirm account selection")
    print("=" * 48)
    print(f"  Email:  {info.get('email', 'N/A')}")
    print(f"  Mode:   {info.get('mode', 'N/A')}")

    if info.get("selected_server"):
        print(f"  Server: {info['selected_server']}")
    else:
        print("  Server: (auto-detect from lobby)")

    if info.get("gf_token"):
        gf_preview = info["gf_token"][:8] + "..."
        print(f"  GF Token: {gf_preview} (saved — may skip login)")
    else:
        print("  GF Token: (none — will do full login)")

    if info.get("blackbox_token"):
        bb_preview = info["blackbox_token"][:20] + "..."
        print(f"  BB Token: {bb_preview} (saved)")
    else:
        print("  BB Token: (will fetch during login)")

    if info.get("proxy"):
        proxy = info["proxy"]
        host = proxy.get("host", "?")
        port = proxy.get("port", "?")
        auto = "will activate after login" if info.get("proxy_auto") else "manual"
        print(f"  Proxy:  {host}:{port} ({auto})")
    else:
        print("  Proxy:  none")

    print("=" * 48)
    return read_yes_no("Proceed to login?", default=True)


def run_account_selection() -> Optional[Dict[str, Any]]:
    """Main entry point for the account selection UI.

    Presents mode selection, runs the chosen flow, shows a confirmation
    screen, and returns the account info dict.

    The returned dict contains everything Phase 2 (login) needs:
        mode            : str           ('stored' or 'manual')
        email           : str
        password        : str
        servers         : List[str]
        selected_server : str
        gf_token        : str           (lobby cookie — skips auth if valid)
        blackbox_token  : str           (device fingerprint)
        proxy           : Optional[Dict]
        proxy_auto      : bool

    Returns
    -------
    Optional[Dict[str, Any]]
        Account info dict, or None if the user exits.
    """
    while True:
        clear_screen()
        print("=" * 48)
        print("  autoIkabot -- Account Selection")
        print("=" * 48)
        print()
        print("  1. Use saved accounts (requires master password)")
        print("  2. Enter account details manually (nothing stored)")
        print("  0. Exit")
        print()

        choice = read_choice("Select mode: ", min_val=0, max_val=2)

        if choice == 0:
            return None

        if choice == 1:
            info = _stored_mode_flow()
        elif choice == 2:
            info = _manual_mode_flow()
        else:
            continue

        if info is None:
            # User cancelled — loop back to mode selection
            continue

        if _display_confirmation(info):
            return info
        # User said no to confirmation — loop back to mode selection
