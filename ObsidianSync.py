import os
import subprocess
import sys
import threading
import time
import psutil
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog, simpledialog
import webbrowser
import pyperclip

# ------------------------------------------------
# CONFIG / GLOBALS
# ------------------------------------------------

CONFIG_FILE = "config.txt"  # Stores vault path, Obsidian path, setup_done flag, etc.
config_data = {
    "VAULT_PATH": "",
    "OBSIDIAN_PATH": "",
    "SETUP_DONE": "0"
}

SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_rsa.pub")

root = None  # We will create this conditionally
log_text = None
progress_bar = None

# ------------------------------------------------
# CONFIG HANDLING
# ------------------------------------------------

def load_config():
    """
    Reads config.txt into config_data dict.
    Expected lines like: KEY=VALUE
    """
    if not os.path.exists(CONFIG_FILE):
        return
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                key, val = line.split("=", 1)
                config_data[key.strip()] = val.strip()

def save_config():
    """
    Writes config_data dict to config.txt.
    """
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        for k, v in config_data.items():
            f.write(f"{k}={v}\n")

# ------------------------------------------------
# HELPER FUNCTIONS
# ------------------------------------------------

def run_command(command, cwd=None, timeout=None):
    """
    Runs a shell command, returning (stdout, stderr, return_code).
    Safe to call in a background thread.
    """
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired as e:
        return "", str(e), 1
    except Exception as e:
        return "", str(e), 1
    
def ensure_github_known_host():
    """
    Adds GitHub's RSA key to known_hosts if not already present.
    This prevents the 'Are you sure you want to continue connecting?' prompt.
    
    Best Practice Note:
      - We're automatically trusting 'github.com' here.
      - In a more security-conscious workflow, you'd verify the key's fingerprint
        against GitHub's official documentation before appending.
    """
    # Check if GitHub is already in known_hosts
    known_hosts_path = os.path.expanduser("~/.ssh/known_hosts")
    if os.path.exists(known_hosts_path):
        with open(known_hosts_path, "r", encoding="utf-8") as f:
            if "github.com" in f.read():
                # Already have GitHub host key, nothing to do
                return

    safe_update_log("Adding GitHub to known hosts (ssh-keyscan)...", 32)
    # Fetch GitHub's RSA key and append to known_hosts
    scan_out, scan_err, rc = run_command("ssh-keyscan -t rsa github.com")
    if rc == 0 and scan_out:
        # Ensure .ssh folder exists
        os.makedirs(os.path.expanduser("~/.ssh"), exist_ok=True)
        with open(known_hosts_path, "a", encoding="utf-8") as f:
            f.write(scan_out + "\n")
    else:
        # If this fails, we won't block the user; but we warn them.
        safe_update_log("Warning: Could not fetch GitHub host key automatically.", 32)


def is_obsidian_running():
    """
    Checks if Obsidian.exe is currently running.
    """
    for proc in psutil.process_iter(attrs=['name']):
        if proc.info['name'] and proc.info['name'].lower() == "obsidian.exe":
            return True
    return False

def safe_update_log(message, progress=None):
    if log_text and progress_bar and root.winfo_exists():
        def _update():
            log_text.config(state='normal')
            log_text.insert(tk.END, message + "\n")
            log_text.config(state='disabled')
            log_text.yview_moveto(1)
            if progress is not None:
                progress_bar["value"] = progress
        try:
            root.after(0, _update)
        except Exception as e:
            print("Error scheduling UI update:", e)
    else:
        print(message)



# ------------------------------------------------
# GITHUB SETUP FUNCTIONS
# ------------------------------------------------
def is_git_repo(folder_path):
    """
    Checks if a folder is already a Git repository.
    Returns True if the folder is a Git repo, otherwise False.
    """
    out, err, rc = run_command("git rev-parse --is-inside-work-tree", cwd=folder_path)
    return rc == 0

def initialize_git_repo(vault_path):
    """
    Initializes a Git repository in the selected vault folder if it's not already a repo.
    Also sets the branch to 'main'.
    """
    if not is_git_repo(vault_path):
        safe_update_log("Initializing Git repository in vault...", 15)
        out, err, rc = run_command("git init", cwd=vault_path)
        if rc == 0:
            run_command("git branch -M main", cwd=vault_path)
            safe_update_log("Git repository initialized successfully.", 20)
        else:
            safe_update_log("Error initializing Git repository: " + err, 20)
    else:
        safe_update_log("Vault is already a Git repository.", 20)

def set_github_remote(vault_path):
    """
    Prompts the user to link an existing GitHub repository,
    handling the case where 'origin' already exists.
    Returns True if successful or skipped gracefully, False if an error occurs.
    """

    # First, check if a remote named 'origin' is already set
    existing_remote_url, err, rc = run_command("git remote get-url origin", cwd=vault_path)
    if rc == 0:
        # 'origin' remote already exists
        safe_update_log(f"A remote named 'origin' already exists: {existing_remote_url}", 25)
        override = messagebox.askyesno(
            "Existing Remote",
            f"A remote 'origin' already points to:\n{existing_remote_url}\n\n"
            "Do you want to override it with a new URL?"
        )
        if not override:
            # User wants to keep the existing remote; just skip reconfiguration
            safe_update_log("Keeping the existing 'origin' remote. Skipping new remote configuration.", 25)
            return True
        else:
            # User wants to override the existing 'origin'
            out, err, rc = run_command("git remote remove origin", cwd=vault_path)
            if rc != 0:
                safe_update_log(f"Error removing existing remote: {err}", 25)
                return False
            safe_update_log("Existing 'origin' remote removed.", 25)

    # If we reach here, there's either no 'origin' or we've just removed it
    use_existing_repo = messagebox.askyesno(
        "GitHub Repository",
        "Do you want to link to an existing GitHub repository now?"
    )
    if use_existing_repo:
        repo_url = simpledialog.askstring(
            "GitHub Repository",
            "Enter your GitHub repository URL (e.g., git@github.com:username/repo.git):",
            parent=root
        )
        if repo_url:
            out, err, rc = run_command(f"git remote add origin {repo_url}", cwd=vault_path)
            if rc == 0:
                safe_update_log(f"Git remote 'origin' set to: {repo_url}", 25)
            else:
                safe_update_log(f"Error setting Git remote: {err}", 25)
                return False
        else:
            messagebox.showerror("Error", "Repository URL not provided. Please try again.")
            return False
    else:
        safe_update_log("Skipping GitHub remote setup. You can set this up later manually.", 25)

    return True

def ensure_placeholder_file(vault_path):
    """
    Creates a placeholder file (README.md) in the vault if it doesn't already exist.
    This ensures that there's at least one file to commit.
    """
    import os
    placeholder_path = os.path.join(vault_path, "README.md")
    if not os.path.exists(placeholder_path):
        with open(placeholder_path, "w", encoding="utf-8") as f:
            f.write("# Welcome to your Obsidian Vault\n\nThis placeholder file was generated automatically by Obsidian Sync to initialize the repository.")
        safe_update_log("Placeholder file 'README.md' created, as the vault was empty.", 5)
    else:
        safe_update_log("Placeholder file 'README.md' already exists.", 5)




# ------------------------------------------------
# WIZARD STEPS (Used Only if SETUP_DONE=0)
# ------------------------------------------------

def find_obsidian_path():
    """
    Attempts to locate Obsidian's installation path in common Windows locations.
    If not found, prompts user to locate manually.
    Returns path or None.
    """
    possible_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Obsidian\Obsidian.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Obsidian\Obsidian.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Obsidian\Obsidian.exe")
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path

    # Prompt user to select
    response = messagebox.askyesno("Obsidian Not Found",
                                   "Obsidian not detected in standard locations.\n"
                                   "Locate the Obsidian.exe manually?")
    if response:
        selected_path = filedialog.askopenfilename(
            title="Select Obsidian Executable",
            filetypes=[("Obsidian Executable", "*.exe")]
        )
        if selected_path:
            return selected_path
    return None

def select_vault_path():
    """
    Asks user to select Obsidian Vault folder. Returns path or None if canceled.
    """
    selected = filedialog.askdirectory(title="Select Obsidian Vault Folder")
    return selected if selected else None

def is_git_installed():
    """
    Returns True if Git is installed, else False.
    """
    out, err, rc = run_command("git --version")
    return rc == 0

def test_ssh_connection_sync():
    """
    Synchronously tests SSH to GitHub. Returns True if OK, False otherwise.
    """
    out, err, rc = run_command("ssh -T git@github.com")
    print("DEBUG: SSH OUT:", out)
    print("DEBUG: SSH ERR:", err)
    print("DEBUG: SSH RC:", rc)
    if "successfully authenticated" in (out + err).lower():
        return True
    return False

def re_test_ssh():
    """
    Re-tests the SSH connection in a background thread.
    If successful, automatically performs an initial commit/push if none exists yet.
    """
    def _test_thread():
        safe_update_log("Re-testing SSH connection to GitHub...", 35)
        ensure_github_known_host()  # ensures no prompt for 'yes/no'

        if test_ssh_connection_sync():
            safe_update_log("SSH connection successful!", 40)
            
            # Perform the initial commit/push if there are no local commits yet
            perform_initial_commit_and_push(config_data["VAULT_PATH"])

            # Mark setup as done
            config_data["SETUP_DONE"] = "1"
            save_config()

            safe_update_log("Setup complete! You can now close this window or start sync.", 100)
        else:
            safe_update_log("SSH connection still failed. Check your GitHub key or generate a new one.", 40)

    threading.Thread(target=_test_thread, daemon=True).start()


def perform_initial_commit_and_push(vault_path):
    """
    Checks if the local repository has any commits.
    If not, creates an initial commit and pushes it to the remote 'origin' on the 'main' branch.
    """
    out, err, rc = run_command("git rev-parse HEAD", cwd=vault_path)
    if rc != 0:
        # rc != 0 implies 'git rev-parse HEAD' failed => no commits (unborn branch)
        safe_update_log("No local commits detected. Creating initial commit...", 50)

        # Stage all files
        run_command("git add .", cwd=vault_path)

        # Commit
        out_commit, err_commit, rc_commit = run_command('git commit -m "Initial commit"', cwd=vault_path)
        if rc_commit == 0:
            # Push and set upstream
            out_push, err_push, rc_push = run_command("git push -u origin main", cwd=vault_path)
            if rc_push == 0:
                safe_update_log("Initial commit pushed to remote repository successfully.", 60)
            else:
                safe_update_log(f"Error pushing initial commit: {err_push}", 60)
        else:
            safe_update_log(f"Error committing files: {err_commit}", 60)
    else:
        # We already have at least one commit in this repo
        safe_update_log("Local repository already has commits. Skipping initial commit step.", 50)


# -- SSH Key Generation in Background

def post_generate_ssh_key():
    """
    Runs on main thread after SSH key generation. Opens GitHub keys page.
    """
    #webbrowser.open("https://github.com/settings/keys")
    messagebox.showinfo("SSH Key Generation",
                        "Your SSH key has been generated (if it didn't exist). "
                        "Click the Copy button to copy to the clipboard.\n"
                        "Please add the public key (~/.ssh/id_rsa.pub) to your GitHub account.")

def generate_ssh_key_async(user_email):
    """
    Runs in background thread to avoid blocking the UI.
    """
    key_path = SSH_KEY_PATH.replace("id_rsa.pub", "id_rsa")
    if not os.path.exists(SSH_KEY_PATH):
        safe_update_log("Generating SSH Key...", 25)
        run_command(f'ssh-keygen -t rsa -b 4096 -C "{user_email}" -f "{key_path}" -N ""')
    root.after(0, post_generate_ssh_key)

def generate_ssh_key():
    """
    Prompts for email and starts background thread for SSH key generation.
    """
    user_email = simpledialog.askstring("SSH Key Generation",
                                        "Enter your email address for the SSH key:",
                                        parent=root)
    if not user_email:
        messagebox.showerror("Email Required", "No email address provided. SSH key generation canceled.")
        return
    threading.Thread(target=generate_ssh_key_async, args=(user_email,), daemon=True).start()

def copy_ssh_key():
    """
    Copies the SSH key to clipboard and opens GitHub SSH settings.
    """
    if os.path.exists(SSH_KEY_PATH):
        with open(SSH_KEY_PATH, "r", encoding="utf-8") as key_file:
            ssh_key = key_file.read().strip()
            pyperclip.copy(ssh_key)
        webbrowser.open("https://github.com/settings/keys")
        messagebox.showinfo("SSH Key Copied",
                            "Your SSH key has been copied to the clipboard.\n"
                            "Paste it into GitHub.")
    else:
        messagebox.showerror("Error", "No SSH key found. Generate one first.")

# ------------------------------------------------
# AUTO-SYNC (Used if SETUP_DONE=1)
# ------------------------------------------------

def auto_sync():
    """
    Called if setup is already done. This function:
      - Checks for an initial commit (creates one if needed, adding a placeholder file if the vault is empty).
      - Checks if the remote branch 'main' exists and pushes the initial commit if not.
      - Stashes local changes, pulls updates, and logs file details of the pull.
      - Opens Obsidian and waits until it is closed.
      - Commits any changes made and logs file-level details from the commit.
      - Pushes the changes, logging any network errors.
    """
    vault_path = config_data["VAULT_PATH"]
    obsidian_path = config_data["OBSIDIAN_PATH"]

    if not vault_path or not obsidian_path:
        safe_update_log("Vault path or Obsidian path not set. Please run setup again.")
        return

    def sync_thread():
        # --- Ensure we have at least one local commit ---
        out, err, rc = run_command("git rev-parse HEAD", cwd=vault_path)
        if rc != 0:
            safe_update_log("No local commits detected. Checking if vault is empty...", 5)
            # Call the placeholder creation function
            ensure_placeholder_file(vault_path)
            safe_update_log("Creating an initial commit...", 5)
            run_command("git add .", cwd=vault_path)
            out_commit, err_commit, rc_commit = run_command('git commit -m "Initial commit (auto-sync)"', cwd=vault_path)
            if rc_commit == 0:
                safe_update_log("Initial commit created.", 5)
            else:
                safe_update_log(f"❌ Error creating initial commit: {err_commit}", 5)
                return
        else:
            safe_update_log("Local repository has commits.", 5)


        # --- Check if the remote branch 'main' exists ---
        ls_out, ls_err, ls_rc = run_command("git ls-remote --heads origin main", cwd=vault_path)
        if not ls_out.strip():
            safe_update_log("Remote branch 'main' not found.", 10)
            safe_update_log("Pushing initial commit to create the remote branch...", 10)
            out_push, err_push, rc_push = run_command("git push -u origin main", cwd=vault_path)
            if rc_push == 0:
                safe_update_log("Initial commit pushed successfully to remote repository.", 15)
            else:
                safe_update_log(f"❌ Error pushing initial commit: {err_push}", 15)
                return
        else:
            safe_update_log("Remote branch 'main' exists. Proceeding with pull...", 10)

        # --- Stash local changes ---
        safe_update_log("Stashing local changes (if any)...", 15)
        run_command("git stash", cwd=vault_path)

        # --- Pull latest changes ---
        safe_update_log("Pulling latest changes from GitHub...", 20)
        out, err, rc = run_command("git pull --rebase origin main", cwd=vault_path)
        if rc != 0:
            if "Could not resolve hostname" in err or "network" in err.lower():
                safe_update_log("❌ Network error: Unable to pull changes. Your local changes remain safely stashed.", 30)
            elif "CONFLICT" in (out + err):
                safe_update_log("❌ Merge conflict occurred during pull. Please resolve conflicts manually.", 30)
            else:
                safe_update_log(f"❌ Pull failed: {err}", 30)
            run_command("git stash pop", cwd=vault_path)
            return
        else:
            safe_update_log("Pull completed successfully. Your vault is now updated with remote changes.", 30)
            # Log details from the pull command (if any)
            if out.strip():
                for line in out.splitlines():
                    safe_update_log(f"✓ {line}", None)

        # --- Reapply stashed changes ---
        out, err, rc = run_command("git stash pop", cwd=vault_path)
        if rc != 0 and "No stash" not in err:
            if "CONFLICT" in (out + err):
                safe_update_log("❌ Merge conflict while reapplying your stashed changes. Please resolve manually.", 35)
                return
            else:
                safe_update_log(f"Stash pop error: {err}", 35)
                return
        safe_update_log("Local changes reapplied successfully.", 35)

        # --- Open Obsidian ---
        safe_update_log("Opening Obsidian for editing. Please make your changes and close Obsidian when done.", 40)
        try:
            subprocess.Popen([obsidian_path], shell=True)
        except Exception as e:
            safe_update_log(f"Error launching Obsidian: {e}", 40)
            return
        safe_update_log("Waiting for Obsidian to close...", 45)
        while is_obsidian_running():
            time.sleep(0.5)

        # 6) Commit changes after Obsidian closes
        safe_update_log("Obsidian closed. Committing local changes...", 50)
        run_command("git add .", cwd=vault_path)
        out, err, rc = run_command('git commit -m "Auto sync commit"', cwd=vault_path)
        committed = True
        if rc != 0 and "nothing to commit" in (out + err).lower():
            safe_update_log("No changes detected during this session. Nothing to commit.", 55)
            committed = False
        elif rc != 0:
            safe_update_log(f"❌ Commit failed: {err}", 55)
            return
        else:
            safe_update_log("Local commit successful.", 55)
            # Log file-level details of what was committed
            commit_details, err_details, rc_details = run_command("git diff-tree --no-commit-id --name-status -r HEAD", cwd=vault_path)
            if rc_details == 0 and commit_details.strip():
                for line in commit_details.splitlines():
                    safe_update_log(f"✓ {line}", None)

        # 7) Push changes only if there were commits made
        if committed:
            safe_update_log("Pushing changes to GitHub...", 60)
            out, err, rc = run_command("git push origin main", cwd=vault_path)
            if rc != 0:
                if "Could not resolve hostname" in err or "network" in err.lower():
                    safe_update_log("❌ Network error while pushing. Your changes are safely committed locally and will be pushed when an internet connection is available.", 70)
                else:
                    safe_update_log(f"❌ Push failed: {err}", 70)
                return
            safe_update_log("✅ Changes pushed successfully to GitHub.", 70)
        else:
            safe_update_log("No changes to push.", 70)
            
        safe_update_log("Sync complete. You can close this window now.", 100)

    threading.Thread(target=sync_thread, daemon=True).start()


# ------------------------------------------------
# ONE-TIME SETUP WORKFLOW
# ------------------------------------------------

def run_setup_wizard():
    """
    Runs the wizard in the main thread:
      1) Ask/find Obsidian.
      2) Ask for Vault.
      3) Check Git installation.
      4) Initialize Git repository and set GitHub remote.
      5) Check/Generate SSH key and Test SSH.
      6) If everything OK, mark SETUP_DONE=1.
    """
    safe_update_log("Running first-time setup...", 0)

    # 1) Find Obsidian
    obsidian_path = find_obsidian_path()
    if not obsidian_path:
        messagebox.showerror("Setup Aborted", "Obsidian not found. Exiting.")
        return
    config_data["OBSIDIAN_PATH"] = obsidian_path
    safe_update_log(f"Obsidian found: {obsidian_path}", 5)

    # 2) Vault path selection
    load_config()  # Load any existing configuration
    if not config_data["VAULT_PATH"]:
        vault = select_vault_path()
        if not vault:
            messagebox.showerror("Setup Aborted", "No vault folder selected. Exiting.")
            return
        config_data["VAULT_PATH"] = vault
    safe_update_log(f"Vault path set: {config_data['VAULT_PATH']}", 10)

    # 3) Check Git installation
    safe_update_log("Checking Git installation...", 15)
    if not is_git_installed():
        messagebox.showerror("Setup Aborted", "Git is not installed. Please install Git and re-run.")
        return
    safe_update_log("Git is installed.", 20)

    # 4) Initialize Git repository in vault if needed
    initialize_git_repo(config_data["VAULT_PATH"])

    # 5) Set up GitHub remote (link an existing repository)
    if not set_github_remote(config_data["VAULT_PATH"]):
        return

    # 6) SSH Key Check/Generation
    safe_update_log("Checking SSH key...", 25)
    if not os.path.exists(SSH_KEY_PATH):
        resp = messagebox.askyesno("SSH Key Missing",
                                   "No SSH key found.\nDo you want to generate one now?")
        if resp:
            generate_ssh_key()  # Runs in a background thread
            safe_update_log("Please add the generated key to GitHub, then click 'Re-test SSH'.", 30)
        else:
            messagebox.showwarning("SSH Key Required", 
                                   "You must generate or provide an SSH key for GitHub sync.")
    else:
        safe_update_log("SSH key found. Make sure it's added to GitHub if you haven't already.", 30)

    # 7) Test SSH connection
    re_test_ssh()

   
# ------------------------------------------------
# MAIN ENTRY POINT
# ------------------------------------------------

def main():
    load_config()

    # If setup is done, run auto-sync in a minimal/no-UI approach
    # But if you still want a log window, we can create a small UI. 
    # We'll do this: if SETUP_DONE=0, show the wizard UI. If =1, show a minimal UI with auto-sync logs.
    if config_data["SETUP_DONE"] == "1":
        # Already set up: run auto-sync with a minimal window or even no window.
        # If you truly want NO window at all, you can remove the UI entirely.
        # But let's provide a small log window for user feedback.
        create_minimal_ui(auto_run=True)
        auto_sync()
    else:
        # Not set up yet: run the wizard UI
        create_wizard_ui()
        run_setup_wizard()

    root.mainloop()

def create_minimal_ui(auto_run=False):
    global root, log_text, progress_bar
    root = tk.Tk()
    root.title("Obsidian Sync" if auto_run else "Obsidian Setup")
    root.geometry("500x300")
    root.configure(bg="#1e1e1e")

    # Create a log area and make it read-only
    log_text = scrolledtext.ScrolledText(root, height=10, width=58, bg="#282828", fg="white")
    log_text.pack(pady=5)
    log_text.config(state='disabled')  # Make it read-only

    progress_bar = ttk.Progressbar(root, orient="horizontal", length=450, mode="determinate")
    progress_bar.pack(pady=5)


    # If you truly want to hide it, do: root.withdraw()

def create_wizard_ui():
    """
    Creates a larger UI with wizard-related buttons.
    """
    global root, log_text, progress_bar
    root = tk.Tk()
    root.title("Obsidian Sync Setup")
    root.geometry("550x400")
    root.configure(bg="#1e1e1e")

    info_label = tk.Label(root, text="Obsidian First-Time Setup", font=("Arial", 14), bg="#1e1e1e", fg="white")
    info_label.pack(pady=5)

    log_text = scrolledtext.ScrolledText(root, height=10, width=60, bg="#282828", fg="white")
    log_text.pack(pady=5)

    progress_bar = ttk.Progressbar(root, orient="horizontal", length=500, mode="determinate")
    progress_bar.pack(pady=5)

    # Optional buttons for SSH key generation or copy
    btn_frame = tk.Frame(root, bg="#1e1e1e")
    btn_frame.pack(pady=5)

    gen_btn = tk.Button(btn_frame, text="Generate SSH Key", command=generate_ssh_key, bg="#663399", fg="white")
    gen_btn.grid(row=0, column=0, padx=5)

    copy_btn = tk.Button(btn_frame, text="Copy SSH Key", command=copy_ssh_key, bg="#0066cc", fg="white")
    copy_btn.grid(row=0, column=1, padx=5)

    exit_btn = tk.Button(root, text="Exit", command=root.destroy, bg="#ff4444", fg="white", width=12)
    exit_btn.pack(pady=5)

    test_ssh_again_btn = tk.Button(
        root, text="Re-test SSH", command=re_test_ssh, 
        bg="#00cc66", fg="white"
    )
    test_ssh_again_btn.pack()

    
# ------------------------------------------------
# EXECUTION
# ------------------------------------------------

if __name__ == "__main__":
    main()
