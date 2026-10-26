import os
import shutil
import subprocess
import sys

from src.core.config import ROOT_DIR, load_api_keys, load_env_keys


def get_gh_executable():
    # 1. Check PATH
    gh_path = shutil.which("gh")
    if gh_path:
        return gh_path

    # 2. Check Visual Studio Build Tools Git PATH
    vs_git_path = r"C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\Common7\IDE\CommonExtensions\Microsoft\TeamFoundation\Team Explorer\Git\cmd\gh.exe"
    if os.path.exists(vs_git_path):
        return vs_git_path

    # 3. Check default WinGet installation location
    winget_path = os.path.expandvars(r"%LOCALAPPDATA%\Programs\GitHub CLI\gh.exe")
    if os.path.exists(winget_path):
        return winget_path

    return None


def run_gh_command(args, capture_output=True):
    gh_bin = get_gh_executable()
    if not gh_bin:
        print("[GitHub CLI] Warning: 'gh' CLI executable not found.")
        print("Install via: winget install --id GitHub.cli")
        return None

    cmd = [gh_bin] + args
    try:
        res = subprocess.run(cmd, capture_output=capture_output, text=True, cwd=ROOT_DIR)
        return res
    except Exception as e:
        print(f"[GitHub CLI] Error running command {' '.join(args)}: {e}")
        return None


def check_gh_status():
    gh_bin = get_gh_executable()
    if not gh_bin:
        print("[-] GitHub CLI (gh) is NOT installed on this host.")
        print("[!] Run: winget install --id GitHub.cli")
        return False

    print(f"[+] Found GitHub CLI at: {gh_bin}")
    res = run_gh_command(["auth", "status"])
    if res and res.returncode == 0:
        print("[+] GitHub CLI is authenticated:")
        print(res.stdout)
        return True
    else:
        print("[!] GitHub CLI is installed but not authenticated.")
        print("[!] Run: gh auth login")
        if res and res.stderr:
            print(res.stderr)
        return False


def sync_secrets():
    keys = load_api_keys()
    if not keys:
        print("[!] No secrets found in .env")
        return False

    print(f"Syncing {len(keys)} secret(s) to GitHub Repository Secrets...")
    success_count = 0
    for key, value in keys.items():
        if not value or value.startswith("YOUR_"):
            continue
        print(f"Setting secret: {key}...")
        res = run_gh_command(["secret", "set", key, "-b", value])
        if res and res.returncode == 0:
            print(f"  - {key}: Success")
            success_count += 1
        else:
            err = res.stderr.strip() if res else "Command failed"
            print(f"  - {key}: Failed ({err})")

    print(f"Successfully synced {success_count} secrets.")
    return success_count > 0


def list_workflows():
    print("Fetching GitHub Actions Workflows...")
    res = run_gh_command(["run", "list", "--limit", "5"])
    if res and res.returncode == 0:
        print(res.stdout)
    else:
        err = res.stderr.strip() if res else "Unknown error"
        print(f"Failed to list workflow runs: {err}")


def trigger_workflow(workflow_name="python-ci.yml"):
    print(f"Triggering workflow: {workflow_name}...")
    res = run_gh_command(["workflow", "run", workflow_name])
    if res and res.returncode == 0:
        print(f"[+] Workflow '{workflow_name}' triggered successfully.")
    else:
        err = res.stderr.strip() if res else "Unknown error"
        print(f"[-] Failed to trigger workflow: {err}")


def get_current_repo_visibility():
    res = run_gh_command(["repo", "view", "--json", "isPrivate", "-q", ".isPrivate"])
    if res and res.returncode == 0:
        is_private = res.stdout.strip().lower() == "true"
        return "Private" if is_private else "Public"
    return "Unknown"


def set_repo_visibility(make_public=True):
    visibility_flag = "--public" if make_public else "--private"
    vis_name = "PUBLIC" if make_public else "PRIVATE"

    print(f"[!] Changing repository visibility to {vis_name}...")
    res = run_gh_command(["repo", "edit", visibility_flag, "--confirm"], capture_output=False)
    if res and res.returncode == 0:
        print(f"[+] Successfully updated repo visibility to {vis_name}!")
        return True
    else:
        print(f"[-] Failed to update repo visibility to {vis_name}.")
        return False


def create_release(tag, title=None, notes=None, files=None):
    if not title:
        title = f"Release {tag}"
    if not notes:
        notes = f"Automated release {tag} created via text_to_printer GitHub CLI."

    cmd = ["release", "create", tag, "--title", title, "--notes", notes]
    if files:
        for f in files:
            if os.path.exists(f):
                cmd.append(f)

    print(f"[+] Creating GitHub Release '{title}' ({tag})...")
    res = run_gh_command(cmd)
    return res and res.returncode == 0


def create_or_update_pr(branch_name, title, body, base="main"):
    print(f"[+] Checking for open PR from {branch_name} -> {base}...")
    res = run_gh_command(
        [
            "pr",
            "list",
            "--head",
            branch_name,
            "--base",
            base,
            "--json",
            "number,url",
            "-q",
            ".[0].url",
        ]
    )
    if res and res.returncode == 0 and res.stdout.strip():
        pr_url = res.stdout.strip()
        print(f"[+] Existing PR found: {pr_url}")
        run_gh_command(["pr", "edit", pr_url, "--title", title, "--body", body])
        return pr_url

    print(f"[+] Creating new PR for branch {branch_name}...")
    create_res = run_gh_command(
        ["pr", "create", "--base", base, "--head", branch_name, "--title", title, "--body", body]
    )
    if create_res and create_res.returncode == 0:
        pr_url = create_res.stdout.strip()
        print(f"[+] Pull Request created: {pr_url}")
        return pr_url
    else:
        print("[-] Failed to create PR.")
        return None


def sync_github_token_from_env():
    keys = load_env_keys()
    gh_token = keys.get("GITHUB_TOKEN")
    if gh_token:
        os.environ["GH_TOKEN"] = gh_token
        os.environ["GITHUB_TOKEN"] = gh_token
        return True
    return False


def cli_menu():
    print("=" * 50)
    print("           GITHUB CLI (& ACTIONS) MANAGER")
    print("=" * 50)
    print("[1] Check GitHub CLI Status & Auth")
    print("[2] Sync .env to GitHub Repository Secrets")
    print("[3] View Recent Workflow Runs")
    print("[4] Trigger Python CI Workflow")
    print("[5] Trigger Frontend CI Workflow")
    print("[6] Create GitHub Tagged Release")
    print("[0] Return to Main Menu")
    print("=" * 50)

    choice = input("Select an option [0-6]: ").strip()
    if choice == "1":
        check_gh_status()
    elif choice == "2":
        sync_secrets()
    elif choice == "3":
        list_workflows()
    elif choice == "4":
        trigger_workflow("python-ci.yml")
    elif choice == "5":
        trigger_workflow("frontend-ci.yml")
    elif choice == "6":
        tag = input("Enter release tag (e.g., v1.0.0): ").strip()
        if tag:
            create_release(tag)
    elif choice == "0":
        return
    else:
        print("Invalid choice.")


def main():
    sync_github_token_from_env()
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "check":
            check_gh_status()
        elif cmd == "sync":
            sync_secrets()
        elif cmd == "workflows":
            list_workflows()
        else:
            cli_menu()
    else:
        cli_menu()


if __name__ == "__main__":
    main()
