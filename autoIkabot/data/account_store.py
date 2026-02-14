"""Encrypted account storage with CRUD operations (Phase 1.4).

Accounts are stored as a JSON list encrypted in a single file:
    autoIkabot/data/accounts.enc

File format: see autoIkabot.utils.crypto for the binary layout.

Account data model (dict keys per account):
    email            : str          - Gameforge email address
    password         : str          - Gameforge password
    servers          : list[str]    - Servers this account is on (e.g. ["s59-en"])
    default_server   : str          - Preferred server to connect to
    proxy            : dict | None  - {host, port, username, password}
    proxy_auto       : bool         - Auto-activate proxy after login
    notifications    : dict         - Backend preferences (per-account)
    blackbox_settings: dict         - Token generator config
"""

import json
import os
import pathlib
import stat
from typing import Any, Dict, List, Optional

from autoIkabot.config import ACCOUNTS_FILE
from autoIkabot.utils.crypto import encrypt, decrypt
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

# Type alias for an account record
Account = Dict[str, Any]


def _set_file_permissions(path: pathlib.Path) -> None:
    """Set file to owner-only read/write (0o600) on Linux/Mac.

    On Windows this is a no-op (Windows uses ACLs, not POSIX permissions).

    Parameters
    ----------
    path : pathlib.Path
        Path to the file.
    """
    if os.name != "nt":
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        except OSError as e:
            logger.warning("Could not set file permissions on %s: %s", path, e)


def _new_account_template() -> Account:
    """Return a blank account dict with all required keys.

    Returns
    -------
    Account
        A dict with default/empty values for all fields.
    """
    return {
        "email": "",
        "password": "",
        "servers": [],
        "default_server": "",
        "proxy": None,
        "proxy_auto": False,
        "notifications": {},
        "blackbox_settings": {},
    }


def accounts_file_exists() -> bool:
    """Check if the encrypted accounts file exists on disk.

    Returns
    -------
    bool
    """
    return ACCOUNTS_FILE.exists() and ACCOUNTS_FILE.stat().st_size > 0


def load_accounts(master_password: str) -> List[Account]:
    """Load and decrypt the accounts list from disk.

    Parameters
    ----------
    master_password : str
        The master password used to decrypt.

    Returns
    -------
    List[Account]
        The list of account dicts. Empty list if file does not exist.

    Raises
    ------
    cryptography.exceptions.InvalidTag
        If the password is wrong.
    ValueError
        If the decrypted data is not valid JSON or not a list.
    """
    if not accounts_file_exists():
        logger.info("No accounts file found, returning empty list.")
        return []

    blob = ACCOUNTS_FILE.read_bytes()
    plaintext = decrypt(blob, master_password)
    accounts = json.loads(plaintext.decode("utf-8"))

    if not isinstance(accounts, list):
        raise ValueError("Accounts file is corrupt: expected a JSON list.")

    logger.info("Loaded %d account(s) from encrypted storage.", len(accounts))
    return accounts


def save_accounts(accounts: List[Account], master_password: str) -> None:
    """Encrypt and save the accounts list to disk.

    Uses atomic write (temp file + rename) to prevent data loss if
    the process is killed mid-write.

    Parameters
    ----------
    accounts : List[Account]
        The list of account dicts to save.
    master_password : str
        The master password used to encrypt.
    """
    # Ensure the data directory exists
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    plaintext = json.dumps(accounts, indent=2, ensure_ascii=False).encode("utf-8")
    blob = encrypt(plaintext, master_password)

    # Write to temp file first, then rename (atomic on POSIX)
    tmp_path = ACCOUNTS_FILE.with_suffix(".tmp")
    tmp_path.write_bytes(blob)
    _set_file_permissions(tmp_path)
    tmp_path.replace(ACCOUNTS_FILE)
    _set_file_permissions(ACCOUNTS_FILE)

    logger.info("Saved %d account(s) to encrypted storage.", len(accounts))


def add_account(
    accounts: List[Account],
    email: str,
    password: str,
    servers: Optional[List[str]] = None,
    default_server: str = "",
    proxy: Optional[Dict] = None,
    proxy_auto: bool = False,
) -> List[Account]:
    """Add a new account to the list.

    Parameters
    ----------
    accounts : List[Account]
        Existing accounts list (modified in place and returned).
    email : str
        Gameforge email address.
    password : str
        Gameforge password.
    servers : Optional[List[str]]
        List of server identifiers (e.g. ["s59-en"]).
    default_server : str
        Preferred server.
    proxy : Optional[Dict]
        Proxy configuration dict or None.
    proxy_auto : bool
        Auto-activate proxy after login.

    Returns
    -------
    List[Account]
        The updated list.
    """
    if servers is None:
        servers = []

    account = _new_account_template()
    account["email"] = email
    account["password"] = password
    account["servers"] = servers
    account["default_server"] = default_server or (servers[0] if servers else "")
    account["proxy"] = proxy
    account["proxy_auto"] = proxy_auto
    accounts.append(account)

    logger.info("Added account: %s (%d servers)", email, len(servers))
    return accounts


def remove_account(accounts: List[Account], index: int) -> List[Account]:
    """Remove an account by index.

    Parameters
    ----------
    accounts : List[Account]
        The list (modified in place).
    index : int
        Zero-based index of the account to remove.

    Returns
    -------
    List[Account]
        The updated list.

    Raises
    ------
    IndexError
        If the index is out of range.
    """
    removed = accounts.pop(index)
    logger.info("Removed account: %s", removed.get("email", "unknown"))
    return accounts


def edit_account(accounts: List[Account], index: int, **fields) -> List[Account]:
    """Edit fields of an existing account.

    Only updates keys that already exist in the account template.
    Unknown keys are logged as warnings and ignored.

    Parameters
    ----------
    accounts : List[Account]
        The list (modified in place).
    index : int
        Zero-based index of the account to edit.
    **fields
        Key-value pairs to update.

    Returns
    -------
    List[Account]
        The updated list.
    """
    account = accounts[index]
    for key, value in fields.items():
        if key in account:
            account[key] = value
        else:
            logger.warning("Unknown account field '%s', ignoring.", key)
    logger.info("Edited account [%d]: %s", index, account.get("email", "unknown"))
    return accounts


def list_accounts_summary(accounts: List[Account]) -> List[str]:
    """Return a list of human-readable account summaries (no passwords).

    Parameters
    ----------
    accounts : List[Account]
        The accounts list.

    Returns
    -------
    List[str]
        One string per account, e.g. "user@example.com (s59-en, s12-en) [PROXY]"
    """
    summaries = []
    for acct in accounts:
        email = acct.get("email", "?")
        servers = ", ".join(acct.get("servers", []))
        proxy_tag = " [PROXY]" if acct.get("proxy") else ""
        summaries.append(f"{email} ({servers}){proxy_tag}")
    return summaries
