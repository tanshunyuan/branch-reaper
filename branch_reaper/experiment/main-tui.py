#!/usr/bin/env python3
"""
Branch Reaper - Interactive TUI for managing git branches.

Features:
- View local and remote branches side by side
- Identify orphaned branches (local branches whose remote is gone)
- Delete local and remote branches interactively
- Refresh branch list from remote
"""

import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Label, Static
from textual.coordinate import Coordinate


class BranchStatus(Enum):
    SYNCED = "synced"    # Exists both locally and remotely
    ORPHAN = "orphan"    # Local exists, remote was deleted (GONE)
    LOCAL = "local"      # Only exists locally
    REMOTE = "remote"    # Only exists remotely


@dataclass
class UnifiedBranch:
    """Represents a branch with its local and remote state."""
    name: str
    has_local: bool = False
    has_remote: bool = False
    is_gone: bool = False  # Remote was deleted (tracking shows "gone")
    is_current: bool = False
    is_protected: bool = False  # Protected branches like main, master
    remote_name: Optional[str] = None  # e.g., "origin"
    local_marked: bool = False
    remote_marked: bool = False

    @property
    def status(self) -> BranchStatus:
        if self.has_local and self.has_remote:
            return BranchStatus.SYNCED
        elif self.has_local and self.is_gone:
            return BranchStatus.ORPHAN
        elif self.has_local:
            return BranchStatus.LOCAL
        else:
            return BranchStatus.REMOTE

    @property
    def local_display(self) -> str:
        if self.local_marked:
            return "[DEL]"
        elif self.has_local:
            return "✓" + (" *" if self.is_current else "")
        elif self.is_gone:
            return "GONE"
        else:
            return "nonexistent"

    @property
    def remote_display(self) -> str:
        if self.remote_marked:
            return f"[DEL] {self.remote_name or 'origin'}"
        elif self.has_remote:
            return f"✓ {self.remote_name or 'origin'}"
        elif self.is_gone:
            return "GONE"
        else:
            return "nonexistent"

    @property
    def can_delete_local(self) -> bool:
        return self.has_local and not self.is_current and not self.is_protected

    @property
    def can_delete_remote(self) -> bool:
        return self.has_remote and not self.is_protected


class BranchManager:
    """Handles all git operations."""

    PROTECTED_BRANCHES = {"main", "master", "develop", "development"}

    def __init__(self):
        self.branches: list[UnifiedBranch] = []
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

    def is_protected(self, branch_name: str) -> bool:
        """Check if a branch is protected."""
        return branch_name in self.PROTECTED_BRANCHES

    def load_branches(self):
        """Load all branches and merge local/remote into unified view."""
        branch_map: dict[str, UnifiedBranch] = {}

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
                tracking = None
                is_gone = False
                remote_name = None

                # Parse tracking info
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

                    if tracking and "/" in tracking:
                        remote_name = tracking.split("/")[0]

                branch = UnifiedBranch(
                    name=name,
                    has_local=True,
                    has_remote=tracking is not None and not is_gone,
                    is_gone=is_gone,
                    is_current=is_current,
                    remote_name=remote_name,
                    is_protected=self.is_protected(name),
                )
                branch_map[name] = branch

        # Get remote branches
        success, output = self.run_git("branch", "-r")
        if success and output:
            for line in output.split("\n"):
                line = line.strip()
                if not line or "->" in line:
                    continue

                # Parse remote/branch format
                if "/" in line:
                    remote_name, branch_name = line.split("/", 1)
                else:
                    continue

                if branch_name in branch_map:
                    # Update existing branch
                    branch_map[branch_name].has_remote = True
                    branch_map[branch_name].remote_name = remote_name
                else:
                    # New remote-only branch
                    branch_map[branch_name] = UnifiedBranch(
                        name=branch_name,
                        has_local=False,
                        has_remote=True,
                        remote_name=remote_name,
                        is_protected=self.is_protected(branch_name),
                    )

        self.branches = sorted(branch_map.values(), key=lambda b: (not b.is_protected, b.name))

    def delete_local_branch(self, branch: UnifiedBranch) -> tuple[bool, str]:
        """Delete a local branch."""
        if branch.is_current:
            return False, "Cannot delete current branch"
        if not branch.has_local:
            return False, "No local branch to delete"

        success, output = self.run_git("branch", "-D", branch.name)
        if success:
            branch.has_local = False
            branch.local_marked = False
            return True, f"Deleted local: {branch.name}"
        return False, f"Failed: {output}"

    def delete_remote_branch(self, branch: UnifiedBranch) -> tuple[bool, str]:
        """Delete a remote branch."""
        if not branch.has_remote:
            return False, "No remote branch to delete"

        remote = branch.remote_name or "origin"
        success, output = self.run_git("push", remote, "--delete", branch.name)
        if success:
            branch.has_remote = False
            branch.remote_marked = False
            return True, f"Deleted remote: {remote}/{branch.name}"
        return False, f"Failed: {output}"


class ConfirmDialog(ModalScreen[bool]):
    """A confirmation dialog for deletions."""

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, branches: list[UnifiedBranch]):
        super().__init__()
        self.branches_to_delete = branches

    def compose(self) -> ComposeResult:
        # Build warning messages
        warnings = []
        deletions = []

        for branch in self.branches_to_delete:
            if branch.local_marked:
                deletions.append(f"  • [local] {branch.name}")
                if not branch.has_remote and not branch.is_gone:
                    warnings.append(f"  ⚠ '{branch.name}' only exists locally - deletion is permanent!")
                elif branch.is_gone:
                    warnings.append(f"  ⚠ '{branch.name}' has no remote backup - deletion is permanent!")

            if branch.remote_marked:
                deletions.append(f"  • [remote] {branch.remote_name or 'origin'}/{branch.name}")
                if not branch.has_local:
                    warnings.append(f"  ⚠ '{branch.name}' has no local copy - this work may be lost!")

        content = "[bold]Branches to delete:[/bold]\n" + "\n".join(deletions)

        if warnings:
            content += "\n\n[bold red]Warnings:[/bold red]\n" + "\n".join(warnings)

        content += "\n\n[bold]Proceed? (y/n)[/bold]"

        with Container(id="dialog"):
            yield Label(content, id="dialog-content")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class BranchReaperApp(App):
    """The main Branch Reaper TUI application."""

    CSS = """
    Screen {
        background: $surface;
    }

    #title {
        dock: top;
        height: 3;
        content-align: center middle;
        background: $primary;
        color: $text;
        text-style: bold;
    }

    #status {
        dock: bottom;
        height: 1;
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
    }

    DataTable {
        height: 1fr;
    }

    DataTable > .datatable--cursor {
        background: $secondary;
        color: $text;
    }

    #dialog {
        align: center middle;
        width: 60;
        height: auto;
        max-height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #dialog-content {
        width: 100%;
        height: auto;
    }

    .warning {
        color: $warning;
    }
    """

    BINDINGS = [
        Binding("up", "cursor_up", "↑", show=False),
        Binding("down", "cursor_down", "↓", show=False),
        Binding("left", "move_left", "←", show=False),
        Binding("right", "move_right", "→", show=False),
        Binding("space", "toggle_mark", "Mark/Unmark"),
        Binding("d", "delete", "Delete"),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.manager = BranchManager()
        self.current_column = 1  # 0=Branch, 1=Local, 2=Remote, 3=Status (start on Local)
        self.status_message = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("🌾 Branch Reaper    [dim]Navigate: ←↑↓→  Mark: Space  Delete: d  Refresh: r  Quit: q[/dim]", id="title")
        yield DataTable(id="branch-table")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the application."""
        if not self.manager.is_git_repo():
            self.status_message = "Error: Not a git repository!"
            self.update_status()
            return

        # Setup table first
        table = self.query_one("#branch-table", DataTable)
        table.cursor_type = "cell"
        table.add_columns("Branch", "Local", "Remote", "Status")

        # Show loading state
        self.status_message = "⏳ Fetching from remote..."
        self.update_status()
        self.refresh()  # Force UI update

        # Use call_later to allow UI to render before blocking git operations
        self.call_later(self.initial_load)

    def initial_load(self) -> None:
        """Perform initial git fetch and load branches."""
        self.manager.fetch_prune()
        self.manager.load_branches()

        self.refresh_table()

        # Position cursor on Local column
        if self.manager.branches:
            table = self.query_one("#branch-table", DataTable)
            table.move_cursor(row=0, column=1)

        self.status_message = f"✓ Loaded {len(self.manager.branches)} branches"
        self.update_status()

    def update_row(self, row_index: int) -> None:
        """Update a single row in the table."""
        if row_index < 0 or row_index >= len(self.manager.branches):
            return

        table = self.query_one("#branch-table", DataTable)
        branch = self.manager.branches[row_index]

        # Status color
        status_style = {
            BranchStatus.SYNCED: "green",
            BranchStatus.ORPHAN: "yellow",
            BranchStatus.LOCAL: "cyan",
            BranchStatus.REMOTE: "blue",
        }.get(branch.status, "white")

        # Protected branches shown dimmed
        if branch.is_protected:
            name_display = f"[dim]{branch.name} 🔒[/dim]"
            local_display = f"[dim]{branch.local_display}[/dim]"
            remote_display = f"[dim]{branch.remote_display}[/dim]"
            status_display = f"[dim]{branch.status.value}[/dim]"
        else:
            name_display = branch.name

            local_style = "red bold" if branch.local_marked else ("dim" if not branch.has_local else "")
            remote_style = "red bold" if branch.remote_marked else ("dim" if not branch.has_remote else "")

            local_display = f"[{local_style}]{branch.local_display}[/]" if local_style else branch.local_display
            remote_display = f"[{remote_style}]{branch.remote_display}[/]" if remote_style else branch.remote_display
            status_display = f"[{status_style}]{branch.status.value}[/]"

        # Update each cell in the row
        row_key = table.get_row_at(row_index)
        table.update_cell(row_key, "Branch", name_display)
        table.update_cell(row_key, "Local", local_display)
        table.update_cell(row_key, "Remote", remote_display)
        table.update_cell(row_key, "Status", status_display)

    def refresh_table(self) -> None:
        """Refresh the table with current branch data."""
        table = self.query_one("#branch-table", DataTable)
        table.clear()

        for branch in self.manager.branches:
            # Status color
            status_style = {
                BranchStatus.SYNCED: "green",
                BranchStatus.ORPHAN: "yellow",
                BranchStatus.LOCAL: "cyan",
                BranchStatus.REMOTE: "blue",
            }.get(branch.status, "white")

            # Protected branches shown dimmed
            if branch.is_protected:
                name_display = f"[dim]{branch.name} 🔒[/dim]"
                local_display = f"[dim]{branch.local_display}[/dim]"
                remote_display = f"[dim]{branch.remote_display}[/dim]"
                status_display = f"[dim]{branch.status.value}[/dim]"
            else:
                name_display = branch.name

                # Style the cells based on state
                local_style = "red bold" if branch.local_marked else ("dim" if not branch.has_local else "")
                remote_style = "red bold" if branch.remote_marked else ("dim" if not branch.has_remote else "")

                local_display = f"[{local_style}]{branch.local_display}[/]" if local_style else branch.local_display
                remote_display = f"[{remote_style}]{branch.remote_display}[/]" if remote_style else branch.remote_display
                status_display = f"[{status_style}]{branch.status.value}[/]"

            table.add_row(name_display, local_display, remote_display, status_display)

    def update_status(self) -> None:
        """Update the status bar."""
        marked_count = sum(
            (1 if b.local_marked else 0) + (1 if b.remote_marked else 0)
            for b in self.manager.branches
        )

        status = self.query_one("#status", Static)
        if marked_count > 0:
            status.update(f"{self.status_message} | Marked for deletion: {marked_count}")
        else:
            status.update(self.status_message)

    def action_move_left(self) -> None:
        """Move cursor to Local column."""
        table = self.query_one("#branch-table", DataTable)
        row = table.cursor_row
        table.move_cursor(row=row, column=1)
        self.current_column = 1

    def action_move_right(self) -> None:
        """Move cursor to Remote column."""
        table = self.query_one("#branch-table", DataTable)
        row = table.cursor_row
        table.move_cursor(row=row, column=2)
        self.current_column = 2

    def action_toggle_mark(self) -> None:
        """Toggle deletion mark on current cell."""
        table = self.query_one("#branch-table", DataTable)
        row = table.cursor_row
        col = table.cursor_column

        if row < 0 or row >= len(self.manager.branches):
            return

        branch = self.manager.branches[row]

        if col == 1:  # Local column
            if branch.is_protected:
                self.status_message = f"Cannot delete protected branch: {branch.name}"
            elif branch.can_delete_local:
                branch.local_marked = not branch.local_marked
                self.status_message = f"{'Marked' if branch.local_marked else 'Unmarked'} local: {branch.name}"
            elif branch.is_current:
                self.status_message = "Cannot delete current branch"
            else:
                self.status_message = "No local branch to delete"
        elif col == 2:  # Remote column
            if branch.is_protected:
                self.status_message = f"Cannot delete protected branch: {branch.name}"
            elif branch.can_delete_remote:
                branch.remote_marked = not branch.remote_marked
                self.status_message = f"{'Marked' if branch.remote_marked else 'Unmarked'} remote: {branch.name}"
            else:
                self.status_message = "No remote branch to delete"
        else:
            self.status_message = "Use ← → to select Local or Remote column"

        self.update_row(row)
        self.update_status()

    def action_refresh(self) -> None:
        """Refresh branches from remote."""
        self.status_message = "⏳ Refreshing from remote..."
        self.update_status()
        self.refresh()  # Force UI update

        # Use call_later to allow UI to render
        self.call_later(self.do_refresh)

    def do_refresh(self) -> None:
        """Perform the actual refresh operation."""
        success, message = self.manager.fetch_prune()
        self.manager.load_branches()
        self.refresh_table()

        if success:
            self.status_message = f"✓ {message}"
        else:
            self.status_message = f"✗ {message}"
        self.update_status()

    def action_delete(self) -> None:
        """Delete marked branches."""
        # Check if any branches can be deleted at all
        deletable_branches = [
            b for b in self.manager.branches
            if b.can_delete_local or b.can_delete_remote
        ]

        if not deletable_branches:
            self.status_message = "⚠ No branches can be deleted (all protected or current)"
            self.update_status()
            return

        marked = [b for b in self.manager.branches if b.local_marked or b.remote_marked]

        if not marked:
            self.status_message = "No branches marked for deletion (use Space to mark)"
            self.update_status()
            return

        # Show confirmation dialog
        self.push_screen(ConfirmDialog(marked), self.handle_delete_confirm)

    def handle_delete_confirm(self, confirmed: bool) -> None:
        """Handle the result of the confirmation dialog."""
        if not confirmed:
            self.status_message = "Deletion cancelled"
            self.update_status()
            return

        deleted = 0
        errors = []

        for branch in self.manager.branches[:]:  # Copy list since we modify during iteration
            if branch.local_marked:
                success, msg = self.manager.delete_local_branch(branch)
                if success:
                    deleted += 1
                else:
                    errors.append(msg)

            if branch.remote_marked:
                success, msg = self.manager.delete_remote_branch(branch)
                if success:
                    deleted += 1
                else:
                    errors.append(msg)

        # Remove branches that no longer exist anywhere
        self.manager.branches = [
            b for b in self.manager.branches
            if b.has_local or b.has_remote
        ]

        self.refresh_table()

        if errors:
            self.status_message = f"Deleted {deleted}, Errors: {len(errors)}"
        else:
            self.status_message = f"Successfully deleted {deleted} branch(es)"

        self.update_status()

    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()


def main():
    app = BranchReaperApp()
    app.run()


if __name__ == "__main__":
    main()
