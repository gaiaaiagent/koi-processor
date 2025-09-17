#!/usr/bin/env python3
"""
Interactive Review Interface for Quality Control
Provides CLI interface for Gregory to review and approve content
"""

import asyncio
import sys
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich import print as rprint
from loguru import logger

# Add parent directories to path
sys.path.append(str(Path(__file__).parent.parent))
sys.path.append(str(Path(__file__).parent.parent.parent / "koi-sensors"))

from quality_control import QualityControl, ContentType, ApprovalStatus

# Initialize Rich console
console = Console()


class ReviewInterface:
    """
    Interactive CLI interface for content review and approval
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize the review interface"""
        self.qc = QualityControl(config_path)
        self.current_review = None
        self.reviewer_name = "Gregory"  # Default reviewer
        
    async def run(self):
        """Main interface loop"""
        # Initialize database
        await self.qc.initialize_db()
        
        # Welcome message
        console.print("\n[bold cyan]🎯 Regen Network Quality Control Review Interface[/bold cyan]\n")
        console.print("Welcome to the content review and approval system.")
        console.print("This interface allows you to review and approve daily threads and weekly digests.\n")
        
        # Get reviewer name
        self.reviewer_name = Prompt.ask("Enter your name", default="Gregory")
        console.print(f"\nHello, [bold]{self.reviewer_name}[/bold]!\n")
        
        while True:
            try:
                await self.show_main_menu()
            except KeyboardInterrupt:
                console.print("\n[yellow]Exiting review interface...[/yellow]")
                break
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                logger.error(f"Interface error: {e}")
        
        # Cleanup
        await self.qc.cleanup()
    
    async def show_main_menu(self):
        """Display main menu and handle selection"""
        console.print("\n[bold]Main Menu[/bold]")
        console.print("1. View pending reviews")
        console.print("2. Review specific content")
        console.print("3. View approval statistics")
        console.print("4. Check auto-publish status")
        console.print("5. View rollback history")
        console.print("6. Exit")
        
        choice = Prompt.ask("\nSelect an option", choices=["1", "2", "3", "4", "5", "6"])
        
        if choice == "1":
            await self.view_pending_reviews()
        elif choice == "2":
            await self.review_specific_content()
        elif choice == "3":
            await self.view_statistics()
        elif choice == "4":
            await self.check_auto_publish()
        elif choice == "5":
            await self.view_rollback_history()
        elif choice == "6":
            raise KeyboardInterrupt
    
    async def view_pending_reviews(self):
        """Display list of pending reviews"""
        console.print("\n[bold]Pending Reviews[/bold]\n")
        
        reviews = await self.qc.get_pending_reviews(limit=20)
        
        if not reviews:
            console.print("[green]No pending reviews![/green]")
            return
        
        # Create table
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=4)
        table.add_column("Review ID", width=12)
        table.add_column("Type", width=15)
        table.add_column("Style Score", justify="right", width=12)
        table.add_column("Validation", justify="right", width=12)
        table.add_column("Auto-Publish", width=12)
        table.add_column("Created", width=20)
        
        for i, review in enumerate(reviews, 1):
            # Color code scores
            style_color = self._get_score_color(review['style_score'])
            val_color = self._get_score_color(review['validation_score'])
            
            table.add_row(
                str(i),
                review['review_id'][:8] + "...",
                review['content_type'],
                f"[{style_color}]{review['style_score']:.2f}[/{style_color}]",
                f"[{val_color}]{review['validation_score']:.2f}[/{val_color}]",
                "✅" if review['auto_publish_eligible'] else "❌",
                review['created_at'][:19]
            )
        
        console.print(table)
        
        # Ask to review one
        if Confirm.ask("\nWould you like to review one of these?"):
            try:
                idx = int(Prompt.ask("Enter the number", default="1")) - 1
                if 0 <= idx < len(reviews):
                    await self.review_content(reviews[idx]['review_id'])
                else:
                    console.print("[red]Invalid selection[/red]")
            except ValueError:
                console.print("[red]Invalid number[/red]")
    
    async def review_specific_content(self):
        """Review a specific content by ID"""
        review_id = Prompt.ask("\nEnter Review ID (or 'back' to return)")
        
        if review_id.lower() == 'back':
            return
        
        await self.review_content(review_id)
    
    async def review_content(self, review_id: str):
        """Review and approve/reject specific content"""
        console.print(f"\n[bold]Reviewing Content: {review_id}[/bold]\n")
        
        # Get review details
        review = await self.qc.get_review(review_id)
        
        if not review:
            console.print(f"[red]Review {review_id} not found[/red]")
            return
        
        # Display content info
        info_panel = Panel(
            f"""[bold]Content Type:[/bold] {review['content_type']}
[bold]Style Score:[/bold] {review['style_score']:.2f}
[bold]Validation Score:[/bold] {review['validation_score']:.2f}
[bold]Status:[/bold] {review['approval_status']}
[bold]Auto-Publish:[/bold] {'Yes' if review['auto_publish_eligible'] else 'No'}
[bold]Created:[/bold] {review['created_at']}""",
            title="Review Information",
            border_style="blue"
        )
        console.print(info_panel)
        
        # Display quality issues if any
        if review['quality_issues']:
            self._display_quality_issues(review['quality_issues'])
        
        # Display content
        self._display_content(review['content_data'], review['content_type'])
        
        # Review actions
        if review['approval_status'] in ['draft', 'pending_review']:
            await self._handle_review_actions(review_id)
        else:
            console.print(f"\n[yellow]Content already {review['approval_status']}[/yellow]")
    
    def _display_quality_issues(self, issues: Dict[str, Any]):
        """Display quality issues in a formatted way"""
        console.print("\n[bold red]Quality Issues:[/bold red]")
        
        if 'validation' in issues:
            validation = issues['validation']
            if validation.get('issues'):
                console.print("\n[red]Validation Issues:[/red]")
                for issue in validation['issues']:
                    console.print(f"  • {issue}")
            
            if validation.get('warnings'):
                console.print("\n[yellow]Warnings:[/yellow]")
                for warning in validation['warnings']:
                    console.print(f"  • {warning}")
    
    def _display_content(self, content_data: Dict[str, Any], content_type: str):
        """Display the actual content being reviewed"""
        console.print("\n[bold]Content:[/bold]\n")
        
        if content_type == "daily_thread":
            # Display thread posts
            if 'posts' in content_data:
                for i, post in enumerate(content_data['posts'], 1):
                    console.print(f"[bold]Post {i}:[/bold]")
                    console.print(Panel(post.get('content', ''), border_style="dim"))
                    console.print(f"Characters: {post.get('char_count', len(post.get('content', '')))}\n")
        
        elif content_type == "weekly_digest":
            # Display brief
            if 'brief' in content_data:
                # Use Markdown rendering for better display
                md = Markdown(content_data['brief'])
                console.print(md)
            elif 'summary' in content_data:
                console.print(content_data['summary'])
        
        else:
            # Generic display
            console.print(json.dumps(content_data, indent=2))
    
    async def _handle_review_actions(self, review_id: str):
        """Handle approve/reject actions"""
        console.print("\n[bold]Review Actions:[/bold]")
        console.print("1. ✅ Approve")
        console.print("2. ❌ Reject")
        console.print("3. 📝 Request changes")
        console.print("4. ⬅️  Skip (return to menu)")
        
        action = Prompt.ask("\nSelect action", choices=["1", "2", "3", "4"])
        
        if action == "1":
            # Approve
            notes = Prompt.ask("Approval notes (optional)", default="")
            success = await self.qc.approve_content(
                review_id=review_id,
                reviewer=self.reviewer_name,
                notes=notes or "Approved for publication"
            )
            if success:
                console.print("[green]✅ Content approved![/green]")
            else:
                console.print("[red]Failed to approve content[/red]")
        
        elif action == "2":
            # Reject
            notes = Prompt.ask("Rejection reason")
            success = await self.qc.reject_content(
                review_id=review_id,
                reviewer=self.reviewer_name,
                notes=notes
            )
            if success:
                console.print("[red]❌ Content rejected[/red]")
            else:
                console.print("[red]Failed to reject content[/red]")
        
        elif action == "3":
            # Request changes
            notes = Prompt.ask("What changes are needed?")
            success = await self.qc.reject_content(
                review_id=review_id,
                reviewer=self.reviewer_name,
                notes=f"Changes requested: {notes}"
            )
            if success:
                console.print("[yellow]📝 Changes requested[/yellow]")
            else:
                console.print("[red]Failed to request changes[/red]")
    
    async def view_statistics(self):
        """Display approval statistics"""
        console.print("\n[bold]Approval Statistics[/bold]\n")
        
        # Get stats for different periods
        periods = [(7, "Last 7 days"), (30, "Last 30 days")]
        
        for days, label in periods:
            stats = await self.qc.get_approval_stats(days=days)
            
            table = Table(title=label, show_header=True, header_style="bold magenta")
            table.add_column("Metric", style="cyan", width=20)
            table.add_column("Value", justify="right")
            
            table.add_row("Total Reviews", str(stats['total_reviews']))
            table.add_row("Approved", f"[green]{stats['approved']}[/green]")
            table.add_row("Rejected", f"[red]{stats['rejected']}[/red]")
            table.add_row("Published", f"[blue]{stats['published']}[/blue]")
            table.add_row("Auto-Published", f"[cyan]{stats['auto_published']}[/cyan]")
            table.add_row("Rolled Back", f"[yellow]{stats['rolled_back']}[/yellow]")
            table.add_row("Pending", f"[dim]{stats['pending']}[/dim]")
            table.add_row("", "")  # Separator
            table.add_row("Avg Style Score", f"{stats['avg_style_score']:.2f}")
            table.add_row("Avg Validation Score", f"{stats['avg_validation_score']:.2f}")
            
            console.print(table)
            console.print()
    
    async def check_auto_publish(self):
        """Check and manage auto-publish settings"""
        console.print("\n[bold]Auto-Publish Status[/bold]\n")
        
        # Display current settings
        config = self.qc.auto_publish_config
        enabled = self.qc.auto_publish_enabled
        
        status_panel = Panel(
            f"""[bold]Enabled:[/bold] {'✅ Yes' if enabled else '❌ No'}
[bold]After Days:[/bold] {config.get('after_days', 7)}
[bold]Min Consecutive Approvals:[/bold] {config.get('min_consecutive_approvals', 5)}
[bold]Quality Threshold:[/bold] {config.get('quality_threshold', 0.85)}
[bold]Start Date:[/bold] {self.qc.auto_publish_start_date or 'Not set'}""",
            title="Auto-Publish Configuration",
            border_style="cyan"
        )
        console.print(status_panel)
        
        if enabled:
            # Check for eligible content
            console.print("\n[bold]Checking for auto-publishable content...[/bold]")
            published = await self.qc.auto_publish_check()
            
            if published:
                console.print(f"\n[green]Auto-published {len(published)} items:[/green]")
                for item_id in published:
                    console.print(f"  • {item_id}")
            else:
                console.print("[yellow]No content eligible for auto-publish[/yellow]")
        
        # Ask to toggle
        if Confirm.ask(f"\nWould you like to {'disable' if enabled else 'enable'} auto-publish?"):
            self.qc.auto_publish_enabled = not enabled
            console.print(f"[green]Auto-publish {'enabled' if not enabled else 'disabled'}[/green]")
    
    async def view_rollback_history(self):
        """View rollback history"""
        console.print("\n[bold]Rollback History[/bold]\n")
        
        if not self.qc.rollback_history:
            console.print("[green]No rollbacks performed[/green]")
            return
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Review ID", width=12)
        table.add_column("Reason", width=30)
        table.add_column("Rolled Back By", width=15)
        table.add_column("Timestamp", width=20)
        
        for rollback in self.qc.rollback_history:
            table.add_row(
                rollback['review_id'][:8] + "...",
                rollback['reason'][:30] + ("..." if len(rollback['reason']) > 30 else ""),
                rollback['rolled_back_by'],
                rollback['timestamp'][:19]
            )
        
        console.print(table)
    
    def _get_score_color(self, score: float) -> str:
        """Get color based on score value"""
        if score >= 0.9:
            return "green"
        elif score >= 0.7:
            return "yellow"
        else:
            return "red"


async def main():
    """Main entry point"""
    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>",
        level="INFO"
    )
    
    # Check for config file argument
    config_path = None
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
    
    # Run interface
    interface = ReviewInterface(config_path)
    await interface.run()


if __name__ == "__main__":
    asyncio.run(main())