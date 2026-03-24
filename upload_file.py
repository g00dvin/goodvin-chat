"""
Uploads a file to GigaChat storage and prints its file_id.
Set the returned file_id as GIGACHAT_FILE_ID= in your .env.

Usage:
    python upload_file.py <path_to_file>

Examples:
    python upload_file.py handbook.pdf
    python upload_file.py data/faq.docx
"""
import os
import sys
import uuid
import time
import httpx

AUTH_URL  = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
FILES_URL = "https://gigachat.devices.sberbank.ru/api/v1/files"

_MIME = {
    ".pdf":  "application/pdf",
    ".txt":  "text/plain",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".epub": "application/epub+zip",
    ".ppt":  "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _get_token(auth_key: str, scope: str, http: httpx.Client) -> str:
    resp = http.post(
        AUTH_URL,
        headers={
            "Authorization": f"Basic {auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"scope": scope},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"  Auth OK — token expires at {data['expires_at']}")
    return data["access_token"]


def upload(file_path: str, auth_key: str, scope: str) -> str:
    if not os.path.isfile(file_path):
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    ext  = os.path.splitext(file_path)[1].lower()
    mime = _MIME.get(ext, "application/octet-stream")
    size = os.path.getsize(file_path)

    print(f"File   : {file_path}")
    print(f"Size   : {size:,} bytes")
    print(f"MIME   : {mime}")
    print()

    with httpx.Client(verify=False, timeout=120) as http:
        print("Step 1 — getting access token...")
        token = _get_token(auth_key, scope, http)

        print("Step 2 — uploading file...")
        with open(file_path, "rb") as fh:
            resp = http.post(
                FILES_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                files={"file": (os.path.basename(file_path), fh, mime)},
                data={"purpose": "general"},
            )

        if not resp.is_success:
            print(f"\nUpload failed: HTTP {resp.status_code}", file=sys.stderr)
            print(resp.text, file=sys.stderr)
            sys.exit(1)

        result = resp.json()
        file_id = result["id"]

    print(f"\n{'=' * 55}")
    print(f"  Upload OK!")
    print(f"  file_id : {file_id}")
    print(f"  filename: {result.get('filename', '—')}")
    print(f"  bytes   : {result.get('bytes', '—'):,}")
    print(f"{'=' * 55}")
    print(f"\nAdd to .env:")
    print(f"  GIGACHAT_FILE_ID={file_id}")
    print()

    return file_id


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    file_path = sys.argv[1]

    auth_key = os.environ.get("GIGACHAT_AUTH_KEY")
    if not auth_key:
        auth_key = input("GIGACHAT_AUTH_KEY (Авторизационные данные): ").strip()

    scope = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    print(f"Scope  : {scope}\n")

    upload(file_path, auth_key, scope)


if __name__ == "__main__":
    main()
