import base64
import subprocess
from datetime import datetime
from sqlmodel import Session

from local_models import LocalRepo
from local_database import engine
from config import config


async def push_to_branch(
    file_path: str, new_content: str, commit_message: str, repo_id: int
) -> str:
    """Pushes a code change to a new branch for human review — never to
    main or master. Use this only after the user explicitly confirms
    they want a suggested change pushed to GitHub. Creates a new branch,
    commits the change, and returns a link for the user to review and
    merge manually. Requires GITHUB_TOKEN to be configured."""
    if not config.github_token:
        return "GITHUB_TOKEN not configured. This feature is optional and currently disabled."

    import httpx

    with Session(engine) as session:
        repo = session.get(LocalRepo, repo_id)
        if not repo:
            return f"Repo {repo_id} not found."
        repo_path = repo.repo_path

    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return "No GitHub remote found for this repo. push_to_branch requires a 'origin' remote pointing to GitHub."

    remote_url = result.stdout.strip()
    repo_name = (
        remote_url.replace("https://github.com/", "")
        .replace("git@github.com:", "")
        .replace(".git", "")
    )

    branch_name = f"ai-suggestion-{int(datetime.now().timestamp())}"
    headers = {
        "Authorization": f"Bearer {config.github_token}",
        "Accept": "application/vnd.github+json",
    }

    try:
        async with httpx.AsyncClient() as client:
            main_ref = await client.get(
                f"https://api.github.com/repos/{repo_name}/git/ref/heads/main",
                headers=headers,
            )
            if main_ref.status_code != 200:
                return f"Could not find main branch on GitHub for {repo_name}. Status: {main_ref.status_code}"
            main_sha = main_ref.json()["object"]["sha"]

            create_branch = await client.post(
                f"https://api.github.com/repos/{repo_name}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch_name}", "sha": main_sha},
            )
            if create_branch.status_code not in (200, 201):
                return f"Failed to create branch: {create_branch.text}"

            existing_file = await client.get(
                f"https://api.github.com/repos/{repo_name}/contents/{file_path}",
                headers=headers,
                params={"ref": branch_name},
            )
            file_sha = (
                existing_file.json().get("sha")
                if existing_file.status_code == 200
                else None
            )

            content_b64 = base64.b64encode(new_content.encode()).decode()
            put_body = {
                "message": commit_message,
                "content": content_b64,
                "branch": branch_name,
            }
            if file_sha:
                put_body["sha"] = file_sha

            commit_result = await client.put(
                f"https://api.github.com/repos/{repo_name}/contents/{file_path}",
                headers=headers,
                json=put_body,
            )
            if commit_result.status_code not in (200, 201):
                return f"Failed to commit file: {commit_result.text}"

        return (
            f"Pushed to branch '{branch_name}'. "
            f"Review at: https://github.com/{repo_name}/compare/main...{branch_name}"
        )

    except Exception as e:
        return f"push_to_branch failed: {str(e)}"
