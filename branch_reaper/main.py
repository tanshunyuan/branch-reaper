import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import questionary
from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

# Custom style for questionary prompts
custom_style = Style([
    ("qmark", "fg:cyan bold"),
    ("question", "fg:white bold"),
    ("answer", "fg:green bold"),
    ("pointer", "fg:cyan bold"),
    ("highlighted", "fg:cyan bold"),
    ("selected", "fg:green"),
    ("separator", "fg:gray"),
    ("instruction", "fg:gray italic"),
])

console = Console()

# Protected branches that cannot be deleted
PROTECTED_BRANCHES = {"main", "master", "develop", "development"}


class BranchType(Enum):
    LOCAL = "local"
    REMOTE = "remote"


@dataclass
class Branch:
    name: str
    branch_type: BranchType
    is_current: bool = False
    tracking: Optional[str] = None
    is_gone: bool = False  # True if tracking remote no longer exists
    commit_hash: Optional[str] = None
    commit_message: Optional[str] = None


class BranchManager:
    def __init__(self):
        self.local_branches: list[Branch] = []
        self.remote_branches: list[Branch] = []
        self.current_branch: Optional[str] = None

    def run_git(self, *args: str) -> tuple[bool, str]:
        """Run a git command and return (success, output)."""
        try:
            result = subprocess.run(
                ["git"] + list(args),
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True, result.stdout.strip()
            return False, result.stderr.strip()
        except FileNotFoundError:
            return False, "git not found"

    def is_git_repo(self) -> bool:
        """Check if current directory is a git repository."""
        success, _ = self.run_git("rev-parse", "--git-dir")
        return success

    def get_repo_name(self) -> str:
        """Get the repository name."""
        success, output = self.run_git("rev-parse", "--show-toplevel")
        if success:
            return output.split("/")[-1]
        return "unknown"

    def fetch_prune(self) -> tuple[bool, str]:
        """Run git fetch --prune."""
        success, output = self.run_git("fetch", "--prune")
        if success:
            return True, "Fetched and pruned successfully"
        return False, f"Fetch failed: {output}"

    def load_branches(self):
        """Load all local and remote branches."""
        self.local_branches = []
        self.remote_branches = []

        # Get current branch
        success, output = self.run_git("branch", "--show-current")
        self.current_branch = output if success else None

        # Get local branches with tracking info
        success, output = self.run_git("branch", "-vv")
        if success and output:
            for line in output.split("\n"):
                if not line.strip():
                    continue

                is_current = line.lstrip().startswith("*")
                clean_line = line.lstrip()
                if clean_line.startswith("* "):
                    clean_line = clean_line[2:]

                parts = clean_line.split()
                if not parts:
                    continue

                name = parts[0]
                commit_hash = parts[1] if len(parts) > 1 else None
                tracking = None
                is_gone = False

                # Parse tracking info from [origin/branch] or [origin/branch: gone]
                if "[" in clean_line and "]" in clean_line:
                    start = clean_line.index("[")
                    end = clean_line.index("]")
                    tracking_info = clean_line[start + 1 : end]

                    if ": gone" in tracking_info:
                        is_gone = True
                        tracking = tracking_info.split(":")[0].strip()
                    elif ":" in tracking_info:
                        tracking = tracking_info.split(":")[0].strip()
                    else:
                        tracking = tracking_info.strip()

                # Get commit message (everything after the tracking info or hash)
                commit_message = None
                if "]" in clean_line:
                    commit_message = clean_line[clean_line.index("]") + 1:].strip()
                elif len(parts) > 2:
                    commit_message = " ".join(parts[2:])

                self.local_branches.append(
                    Branch(
                        name=name,
                        branch_type=BranchType.LOCAL,
                        is_current=is_current,
                        tracking=tracking,
                        is_gone=is_gone,
                        commit_hash=commit_hash,
                        commit_message=commit_message,
                    )
                )

        # Get remote branches
        success, output = self.run_git("branch", "-r")
        if success and output:
            for line in output.split("\n"):
                line = line.strip()
                if not line or "->" in line:  # Skip HEAD pointer
                    continue
                self.remote_branches.append(
                    Branch(name=line, branch_type=BranchType.REMOTE)
                )

    def delete_local_branch(self, branch: Branch, force: bool = False) -> tuple[bool, str]:
        """Delete a local branch."""
        if branch.is_current:
            return False, "Cannot delete current branch"

        flag = "-D" if force else "-d"
        success, output = self.run_git("branch", flag, branch.name)
        if success:
            self.local_branches = [b for b in self.local_branches if b.name != branch.name]
            return True, f"Deleted local branch: {branch.name}"
        return False, f"Failed to delete {branch.name}: {output}"

    def delete_remote_branch(self, branch: Branch) -> tuple[bool, str]:
        """Delete a remote branch."""
        if "/" not in branch.name:
            return False, f"Invalid remote branch format: {branch.name}"

        remote, branch_name = branch.name.split("/", 1)
        success, output = self.run_git("push", remote, "--delete", branch_name)
        if success:
            self.remote_branches = [b for b in self.remote_branches if b.name != branch.name]
            return True, f"Deleted remote branch: {branch.name}"
        return False, f"Failed to delete {branch.name}: {output}"


def print_header(manager: BranchManager):
    """Print the application header."""
    console.print()
    console.print(
        Panel.fit(
            f"[bold cyan]🌾 Branch Reaper[/bold cyan]\n"
            f"[dim]Repository: {manager.get_repo_name()}[/dim]",
            border_style="cyan",
        )
    )
    console.print()



def refresh_from_remote(manager: BranchManager):
    """Fetch and prune from remote."""
    console.print()
    with console.status("[bold cyan]Fetching from remote...[/bold cyan]", spinner="dots"):
        success, message = manager.fetch_prune()

    if success:
        console.print(f"[green]✓[/green] {message}")
        manager.load_branches()
    else:
        console.print(f"[red]✗[/red] {message}")

    console.print()


def delete_local_branches(manager: BranchManager):
    """Interactive deletion of local branches."""
    console.print()

    # Filter out current branch and protected branches
    deletable = [
        b for b in manager.local_branches
        if not b.is_current and b.name not in PROTECTED_BRANCHES
    ]

    if not deletable:
        console.print("[yellow]No branches to delete[/yellow]")
        console.print("[dim]Protected branches (main, master, develop) and current branch are excluded[/dim]")
        console.print()
        input("Press Enter to continue...")
        return

    # Create choices with formatting
    choices = []
    for branch in deletable:
        label = branch.name
        if branch.is_gone:
            label = f"{branch.name} [GONE - remote deleted]"
        choices.append(questionary.Choice(title=label, value=branch.name))

    # Show info about orphaned branches
    orphaned = [b for b in deletable if b.is_gone]
    if orphaned:
        console.print(
            f"[yellow]💡 Tip: {len(orphaned)} branch(es) marked as GONE - "
            f"their remote tracking branch no longer exists[/yellow]"
        )
        console.print()

    selected = questionary.checkbox(
        "Select branches to delete:",
        choices=choices,
        style=custom_style,
        instruction="(Space to select, Enter to confirm)",
    ).ask()

    if not selected:
        console.print("[dim]No branches selected[/dim]")
        console.print()
        return

    # Confirmation
    console.print()
    console.print("[bold]Branches to delete:[/bold]")
    for name in selected:
        console.print(f"  [red]•[/red] {name}")
    console.print()

    if not questionary.confirm(
        f"Delete {len(selected)} branch(es)?",
        default=False,
        style=custom_style,
    ).ask():
        console.print("[dim]Cancelled[/dim]")
        console.print()
        return

    # Delete branches
    console.print()
    for name in selected:
        branch = next((b for b in deletable if b.name == name), None)
        if branch:
            success, message = manager.delete_local_branch(branch, force=True)
            if success:
                console.print(f"[green]✓[/green] {message}")
            else:
                console.print(f"[red]✗[/red] {message}")

    console.print()


def delete_remote_branches(manager: BranchManager):
    """Interactive deletion of remote branches."""
    console.print()

    # Filter out protected branches (check the branch name part after the remote prefix)
    def is_protected(branch_name: str) -> bool:
        if "/" in branch_name:
            name = branch_name.split("/", 1)[1]
            return name in PROTECTED_BRANCHES
        return branch_name in PROTECTED_BRANCHES

    deletable = [b for b in manager.remote_branches if not is_protected(b.name)]

    if not deletable:
        console.print("[yellow]No branches to delete[/yellow]")
        console.print("[dim]Protected branches (main, master, develop) are excluded[/dim]")
        console.print()
        input("Press Enter to continue...")
        return

    # Warning
    console.print(
        Panel(
            "[bold red]⚠ WARNING[/bold red]\n"
            "This will delete branches from the remote server!\n"
            "This action affects the shared repository.",
            border_style="red",
        )
    )
    console.print()

    choices = [
        questionary.Choice(title=b.name, value=b.name)
        for b in deletable
    ]

    selected = questionary.checkbox(
        "Select remote branches to delete:",
        choices=choices,
        style=custom_style,
        instruction="(Space to select, Enter to confirm)",
    ).ask()

    if not selected:
        console.print("[dim]No branches selected[/dim]")
        console.print()
        return

    # Strong confirmation
    console.print()
    console.print("[bold red]Branches to delete FROM REMOTE:[/bold red]")
    for name in selected:
        console.print(f"  [red]•[/red] {name}")
    console.print()

    if not questionary.confirm(
        f"Are you SURE you want to delete {len(selected)} branch(es) from remote?",
        default=False,
        style=custom_style,
    ).ask():
        console.print("[dim]Cancelled[/dim]")
        console.print()
        return

    # Delete branches
    console.print()
    deleted_remotes = []
    for name in selected:
        branch = next((b for b in manager.remote_branches if b.name == name), None)
        if branch:
            success, message = manager.delete_remote_branch(branch)
            if success:
                console.print(f"[green]✓[/green] {message}")
                deleted_remotes.append(name)
            else:
                console.print(f"[red]✗[/red] {message}")

    # Suggest local branch cleanup
    if deleted_remotes:
        console.print()
        local_to_check = []
        for local in manager.local_branches:
            for remote_name in deleted_remotes:
                if "/" in remote_name:
                    remote_branch = remote_name.split("/", 1)[1]
                    if local.name == remote_branch:
                        local_to_check.append(local.name)
                        break

        if local_to_check:
            console.print(
                Panel(
                    "[bold yellow]💡 Note[/bold yellow]\n"
                    "You may want to delete these corresponding local branches:\n"
                    + "\n".join(f"  • {name}" for name in local_to_check),
                    border_style="yellow",
                )
            )

        # Refresh branches after remote deletion
        console.print()
        with console.status("[bold cyan]Refreshing branches...[/bold cyan]", spinner="dots"):
            manager.load_branches()

    console.print()


def display_branches(manager: BranchManager):
    """Display all branches in formatted tables."""
    # Local branches table
    local_table = Table(
        title="[bold]Local Branches[/bold]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    local_table.add_column("", width=2)  # Current indicator
    local_table.add_column("Branch", style="white")
    local_table.add_column("Tracking", style="dim")
    local_table.add_column("Status", width=10)
    local_table.add_column("Last Commit", style="dim", max_width=40)

    for branch in manager.local_branches:
        current = "→" if branch.is_current else ""
        current_style = "green bold" if branch.is_current else ""

        tracking = branch.tracking or "-"

        if branch.is_gone:
            status = Text("GONE", style="yellow bold")
        elif branch.tracking:
            status = Text("tracking", style="green")
        else:
            status = Text("local", style="dim")

        name_style = "green bold" if branch.is_current else ("yellow" if branch.is_gone else "white")

        local_table.add_row(
            Text(current, style=current_style),
            Text(branch.name, style=name_style),
            tracking,
            status,
            branch.commit_message or "",
        )

    console.print(local_table)
    console.print()

    # Remote branches table
    if manager.remote_branches:
        remote_table = Table(
            title="[bold]Remote Branches[/bold]",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
        )
        remote_table.add_column("Branch", style="blue")

        for branch in manager.remote_branches:
            remote_table.add_row(branch.name)

        console.print(remote_table)
        console.print()


def delete_both_branches(manager: BranchManager):
    """Interactive deletion of both local and remote branches together."""
    console.print()

    # Helper to check if branch is protected
    def is_protected(branch_name: str) -> bool:
        if "/" in branch_name:
            name = branch_name.split("/", 1)[1]
            return name in PROTECTED_BRANCHES
        return branch_name in PROTECTED_BRANCHES

    # Get deletable local branches (excluding current and protected)
    deletable_local = [
        b for b in manager.local_branches
        if not b.is_current and b.name not in PROTECTED_BRANCHES
    ]

    # Get deletable remote branches (excluding protected)
    deletable_remote = [b for b in manager.remote_branches if not is_protected(b.name)]

    if not deletable_local and not deletable_remote:
        console.print("[yellow]No branches to delete[/yellow]")
        console.print("[dim]Protected branches (main, master, develop) and current branch are excluded[/dim]")
        console.print()
        input("Press Enter to continue...")
        return

    # Warning for remote deletion
    if deletable_remote:
        console.print(
            Panel(
                "[bold red]⚠ WARNING[/bold red]\n"
                "This view includes remote branches!\n"
                "Deleting remote branches affects the shared repository.",
                border_style="red",
            )
        )
        console.print()

    # Create choices with clear labels
    choices = []

    if deletable_local:
        choices.append(questionary.Separator("── Local Branches ──"))
        for branch in deletable_local:
            label = f"[local] {branch.name}"
            if branch.is_gone:
                label = f"[local] {branch.name} [GONE]"
            choices.append(questionary.Choice(title=label, value=("local", branch.name)))

    if deletable_remote:
        choices.append(questionary.Separator("── Remote Branches ──"))
        for branch in deletable_remote:
            choices.append(questionary.Choice(title=f"[remote] {branch.name}", value=("remote", branch.name)))

    # Show info about orphaned branches
    orphaned = [b for b in deletable_local if b.is_gone]
    if orphaned:
        console.print(
            f"[yellow]💡 Tip: {len(orphaned)} local branch(es) marked as GONE - "
            f"their remote tracking branch no longer exists[/yellow]"
        )
        console.print()

    selected = questionary.checkbox(
        "Select branches to delete:",
        choices=choices,
        style=custom_style,
        instruction="(Space to select, Enter to confirm)",
    ).ask()

    if not selected:
        console.print("[dim]No branches selected[/dim]")
        console.print()
        return

    # Separate local and remote selections
    local_selected = [name for (type_, name) in selected if type_ == "local"]
    remote_selected = [name for (type_, name) in selected if type_ == "remote"]

    # Confirmation
    console.print()
    if local_selected:
        console.print("[bold]Local branches to delete:[/bold]")
        for name in local_selected:
            console.print(f"  [red]•[/red] {name}")
    if remote_selected:
        console.print("[bold red]Remote branches to delete:[/bold red]")
        for name in remote_selected:
            console.print(f"  [red]•[/red] {name}")
    console.print()

    confirm_msg = f"Delete {len(selected)} branch(es)?"
    if remote_selected:
        confirm_msg = f"Delete {len(selected)} branch(es)? (includes {len(remote_selected)} REMOTE)"

    if not questionary.confirm(
        confirm_msg,
        default=False,
        style=custom_style,
    ).ask():
        console.print("[dim]Cancelled[/dim]")
        console.print()
        return

    # Delete local branches
    console.print()
    if local_selected:
        for name in local_selected:
            branch = next((b for b in deletable_local if b.name == name), None)
            if branch:
                success, message = manager.delete_local_branch(branch, force=True)
                if success:
                    console.print(f"[green]✓[/green] {message}")
                else:
                    console.print(f"[red]✗[/red] {message}")

    # Delete remote branches
    if remote_selected:
        for name in remote_selected:
            branch = next((b for b in manager.remote_branches if b.name == name), None)
            if branch:
                success, message = manager.delete_remote_branch(branch)
                if success:
                    console.print(f"[green]✓[/green] {message}")
                else:
                    console.print(f"[red]✗[/red] {message}")

        # Refresh after remote deletion
        console.print()
        with console.status("[bold cyan]Refreshing branches...[/bold cyan]", spinner="dots"):
            manager.load_branches()

    console.print()


def main_menu(manager: BranchManager) -> bool:
    """Show main menu and handle selection. Returns False to exit."""
    display_branches(manager)

    choices = [
        questionary.Choice(title="🗑️  Delete local branches", value="delete_local"),
        questionary.Choice(title="☁️  Delete remote branches", value="delete_remote"),
        questionary.Choice(title="⚔️  Delete local & remote branches", value="delete_both"),
        questionary.Choice(title="🔄 Refresh from remote (git fetch --prune)", value="refresh"),
        questionary.Separator(),
        questionary.Choice(title="👋 Exit", value="exit"),
    ]

    action = questionary.select(
        "What would you like to do?",
        choices=choices,
        style=custom_style,
    ).ask()

    if action is None or action == "exit":
        return False

    if action == "refresh":
        refresh_from_remote(manager)
    elif action == "delete_local":
        delete_local_branches(manager)
    elif action == "delete_remote":
        delete_remote_branches(manager)
    elif action == "delete_both":
        delete_both_branches(manager)

    return True


def main():
    manager = BranchManager()

    # Check if we're in a git repo
    if not manager.is_git_repo():
        console.print("[red]Error: Not a git repository[/red]")
        console.print("Please run this command from within a git repository.")
        sys.exit(1)

    # Initial fetch and load
    print_header(manager)

    with console.status("[bold cyan]Initializing...[/bold cyan]", spinner="dots"):
        manager.fetch_prune()
        manager.load_branches()

    console.print("[green]✓[/green] Ready")
    console.print()

    # Main loop
    try:
        while main_menu(manager):
            console.clear()
            print_header(manager)
    except KeyboardInterrupt:
        pass

    console.print()
    console.print("[cyan]Goodbye! 👋[/cyan]")


if __name__ == "__main__":
    main()
