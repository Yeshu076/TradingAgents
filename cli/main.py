from typing import Optional, Dict, Any
import datetime
import json
import os
import typer
from pathlib import Path
from functools import wraps
from rich.console import Console
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live
from rich.columns import Columns
from rich.markdown import Markdown
from rich.layout import Layout
from rich.text import Text
from rich.table import Table
from collections import deque
import time
from rich.tree import Tree
from rich import box
from rich.align import Align
from rich.rule import Rule

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.strategy_lab import (
    StrategyLabOrchestrator,
    PromotionGovernancePolicy,
    apply_promotion_governance,
)
from tradingagents.ops.healthcheck import run_production_healthcheck
from tradingagents.ops.bootstrap_env import bootstrap_environment, LLM_KEYS
from tradingagents.config import validate_runtime_environment
from tradingagents.core.shutdown_handler import GracefulShutdownManager
from tradingagents.ops.daemon import start_daemon
from tradingagents.execution import (
    TradeIntent,
    execute_trade,
    list_positions,
    close_symbol_position,
    cancel_all_orders,
    get_paper_wallet_snapshot,
    read_journal_tail,
    count_today_executions,
    get_daily_summary,
    PositionManager,
)
from tradingagents.execution.cycle_state import CycleStateStore
from cli.models import AnalystType
from cli.utils import *
from cli.announcements import fetch_announcements, display_announcements
from cli.stats_handler import StatsCallbackHandler

console = Console()

app = typer.Typer(
    name="TradingAgents",
    help="TradingAgents CLI: Multi-Agents LLM Financial Trading Framework",
    add_completion=True,  # Enable shell completion
)


# Create a deque to store recent messages with a maximum length
class MessageBuffer:
    # Fixed teams that always run (not user-selectable)
    FIXED_AGENTS = {
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # Analyst name mapping
    ANALYST_MAPPING = {
        "market": "Market Analyst",
        "social": "Social Analyst",
        "news": "News Analyst",
        "fundamentals": "Fundamentals Analyst",
    }

    # Report section mapping: section -> (analyst_key for filtering, finalizing_agent)
    # analyst_key: which analyst selection controls this section (None = always included)
    # finalizing_agent: which agent must be "completed" for this report to count as done
    REPORT_SECTIONS = {
        "market_report": ("market", "Market Analyst"),
        "sentiment_report": ("social", "Social Analyst"),
        "news_report": ("news", "News Analyst"),
        "fundamentals_report": ("fundamentals", "Fundamentals Analyst"),
        "investment_plan": (None, "Research Manager"),
        "trader_investment_plan": (None, "Trader"),
        "final_trade_decision": (None, "Portfolio Manager"),
    }

    def __init__(self, max_length=100):
        self.messages = deque(maxlen=max_length)
        self.tool_calls = deque(maxlen=max_length)
        self.current_report = None
        self.final_report = None  # Store the complete final report
        self.agent_status = {}
        self.current_agent = None
        self.report_sections = {}
        self.selected_analysts = []
        self._last_message_id = None

    def init_for_analysis(self, selected_analysts):
        """Initialize agent status and report sections based on selected analysts.

        Args:
            selected_analysts: List of analyst type strings (e.g., ["market", "news"])
        """
        self.selected_analysts = [a.lower() for a in selected_analysts]

        # Build agent_status dynamically
        self.agent_status = {}

        # Add selected analysts
        for analyst_key in self.selected_analysts:
            if analyst_key in self.ANALYST_MAPPING:
                self.agent_status[self.ANALYST_MAPPING[analyst_key]] = "pending"

        # Add fixed teams
        for team_agents in self.FIXED_AGENTS.values():
            for agent in team_agents:
                self.agent_status[agent] = "pending"

        # Build report_sections dynamically
        self.report_sections = {}
        for section, (analyst_key, _) in self.REPORT_SECTIONS.items():
            if analyst_key is None or analyst_key in self.selected_analysts:
                self.report_sections[section] = None

        # Reset other state
        self.current_report = None
        self.final_report = None
        self.current_agent = None
        self.messages.clear()
        self.tool_calls.clear()
        self._last_message_id = None

    def get_completed_reports_count(self):
        """Count reports that are finalized (their finalizing agent is completed).

        A report is considered complete when:
        1. The report section has content (not None), AND
        2. The agent responsible for finalizing that report has status "completed"

        This prevents interim updates (like debate rounds) from counting as completed.
        """
        count = 0
        for section in self.report_sections:
            if section not in self.REPORT_SECTIONS:
                continue
            _, finalizing_agent = self.REPORT_SECTIONS[section]
            # Report is complete if it has content AND its finalizing agent is done
            has_content = self.report_sections.get(section) is not None
            agent_done = self.agent_status.get(finalizing_agent) == "completed"
            if has_content and agent_done:
                count += 1
        return count

    def add_message(self, message_type, content):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.messages.append((timestamp, message_type, content))

    def add_tool_call(self, tool_name, args):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((timestamp, tool_name, args))

    def update_agent_status(self, agent, status):
        if agent in self.agent_status:
            self.agent_status[agent] = status
            self.current_agent = agent

    def update_report_section(self, section_name, content):
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()

    def _update_current_report(self):
        # For the panel display, only show the most recently updated section
        latest_section = None
        latest_content = None

        # Find the most recently updated section
        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content
               
        if latest_section and latest_content:
            # Format the current section for display
            section_titles = {
                "market_report": "Market Analysis",
                "sentiment_report": "Social Sentiment",
                "news_report": "News Analysis",
                "fundamentals_report": "Fundamentals Analysis",
                "investment_plan": "Research Team Decision",
                "trader_investment_plan": "Trading Team Plan",
                "final_trade_decision": "Portfolio Management Decision",
            }
            self.current_report = (
                f"### {section_titles[latest_section]}\n{latest_content}"
            )

        # Update the final complete report
        self._update_final_report()

    def _update_final_report(self):
        report_parts = []

        # Analyst Team Reports - use .get() to handle missing sections
        analyst_sections = ["market_report", "sentiment_report", "news_report", "fundamentals_report"]
        if any(self.report_sections.get(section) for section in analyst_sections):
            report_parts.append("## Analyst Team Reports")
            if self.report_sections.get("market_report"):
                report_parts.append(
                    f"### Market Analysis\n{self.report_sections['market_report']}"
                )
            if self.report_sections.get("sentiment_report"):
                report_parts.append(
                    f"### Social Sentiment\n{self.report_sections['sentiment_report']}"
                )
            if self.report_sections.get("news_report"):
                report_parts.append(
                    f"### News Analysis\n{self.report_sections['news_report']}"
                )
            if self.report_sections.get("fundamentals_report"):
                report_parts.append(
                    f"### Fundamentals Analysis\n{self.report_sections['fundamentals_report']}"
                )

        # Research Team Reports
        if self.report_sections.get("investment_plan"):
            report_parts.append("## Research Team Decision")
            report_parts.append(f"{self.report_sections['investment_plan']}")

        # Trading Team Reports
        if self.report_sections.get("trader_investment_plan"):
            report_parts.append("## Trading Team Plan")
            report_parts.append(f"{self.report_sections['trader_investment_plan']}")

        # Portfolio Management Decision
        if self.report_sections.get("final_trade_decision"):
            report_parts.append("## Portfolio Management Decision")
            report_parts.append(f"{self.report_sections['final_trade_decision']}")

        self.final_report = "\n\n".join(report_parts) if report_parts else None


message_buffer = MessageBuffer()


def create_layout():
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_column(
        Layout(name="upper", ratio=3), Layout(name="analysis", ratio=5)
    )
    layout["upper"].split_row(
        Layout(name="progress", ratio=2), Layout(name="messages", ratio=3)
    )
    return layout


def format_tokens(n):
    """Format token count for display."""
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def update_display(layout, spinner_text=None, stats_handler=None, start_time=None):
    # Header with welcome message
    layout["header"].update(
        Panel(
            "[bold green]Welcome to TradingAgents CLI[/bold green]\n"
            "[dim]© [Tauric Research](https://github.com/TauricResearch)[/dim]",
            title="Welcome to TradingAgents",
            border_style="green",
            padding=(1, 2),
            expand=True,
        )
    )

    # Progress panel showing agent status
    progress_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        box=box.SIMPLE_HEAD,  # Use simple header with horizontal lines
        title=None,  # Remove the redundant Progress title
        padding=(0, 2),  # Add horizontal padding
        expand=True,  # Make table expand to fill available space
    )
    progress_table.add_column("Team", style="cyan", justify="center", width=20)
    progress_table.add_column("Agent", style="green", justify="center", width=20)
    progress_table.add_column("Status", style="yellow", justify="center", width=20)

    # Group agents by team - filter to only include agents in agent_status
    all_teams = {
        "Analyst Team": [
            "Market Analyst",
            "Social Analyst",
            "News Analyst",
            "Fundamentals Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # Filter teams to only include agents that are in agent_status
    teams = {}
    for team, agents in all_teams.items():
        active_agents = [a for a in agents if a in message_buffer.agent_status]
        if active_agents:
            teams[team] = active_agents

    for team, agents in teams.items():
        # Add first agent with team name
        first_agent = agents[0]
        status = message_buffer.agent_status.get(first_agent, "pending")
        if status == "in_progress":
            spinner = Spinner(
                "dots", text="[blue]in_progress[/blue]", style="bold cyan"
            )
            status_cell = spinner
        else:
            status_color = {
                "pending": "yellow",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            status_cell = f"[{status_color}]{status}[/{status_color}]"
        progress_table.add_row(team, first_agent, status_cell)

        # Add remaining agents in team
        for agent in agents[1:]:
            status = message_buffer.agent_status.get(agent, "pending")
            if status == "in_progress":
                spinner = Spinner(
                    "dots", text="[blue]in_progress[/blue]", style="bold cyan"
                )
                status_cell = spinner
            else:
                status_color = {
                    "pending": "yellow",
                    "completed": "green",
                    "error": "red",
                }.get(status, "white")
                status_cell = f"[{status_color}]{status}[/{status_color}]"
            progress_table.add_row("", agent, status_cell)

        # Add horizontal line after each team
        progress_table.add_row("─" * 20, "─" * 20, "─" * 20, style="dim")

    layout["progress"].update(
        Panel(progress_table, title="Progress", border_style="cyan", padding=(1, 2))
    )

    # Messages panel showing recent messages and tool calls
    messages_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        expand=True,  # Make table expand to fill available space
        box=box.MINIMAL,  # Use minimal box style for a lighter look
        show_lines=True,  # Keep horizontal lines
        padding=(0, 1),  # Add some padding between columns
    )
    messages_table.add_column("Time", style="cyan", width=8, justify="center")
    messages_table.add_column("Type", style="green", width=10, justify="center")
    messages_table.add_column(
        "Content", style="white", no_wrap=False, ratio=1
    )  # Make content column expand

    # Combine tool calls and messages
    all_messages = []

    # Add tool calls
    for timestamp, tool_name, args in message_buffer.tool_calls:
        formatted_args = format_tool_args(args)
        all_messages.append((timestamp, "Tool", f"{tool_name}: {formatted_args}"))

    # Add regular messages
    for timestamp, msg_type, content in message_buffer.messages:
        content_str = str(content) if content else ""
        if len(content_str) > 200:
            content_str = content_str[:197] + "..."
        all_messages.append((timestamp, msg_type, content_str))

    # Sort by timestamp descending (newest first)
    all_messages.sort(key=lambda x: x[0], reverse=True)

    # Calculate how many messages we can show based on available space
    max_messages = 12

    # Get the first N messages (newest ones)
    recent_messages = all_messages[:max_messages]

    # Add messages to table (already in newest-first order)
    for timestamp, msg_type, content in recent_messages:
        # Format content with word wrapping
        wrapped_content = Text(content, overflow="fold")
        messages_table.add_row(timestamp, msg_type, wrapped_content)

    layout["messages"].update(
        Panel(
            messages_table,
            title="Messages & Tools",
            border_style="blue",
            padding=(1, 2),
        )
    )

    # Analysis panel showing current report
    if message_buffer.current_report:
        layout["analysis"].update(
            Panel(
                Markdown(message_buffer.current_report),
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        layout["analysis"].update(
            Panel(
                "[italic]Waiting for analysis report...[/italic]",
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )

    # Footer with statistics
    # Agent progress - derived from agent_status dict
    agents_completed = sum(
        1 for status in message_buffer.agent_status.values() if status == "completed"
    )
    agents_total = len(message_buffer.agent_status)

    # Report progress - based on agent completion (not just content existence)
    reports_completed = message_buffer.get_completed_reports_count()
    reports_total = len(message_buffer.report_sections)

    # Build stats parts
    stats_parts = [f"Agents: {agents_completed}/{agents_total}"]

    # LLM and tool stats from callback handler
    if stats_handler:
        stats = stats_handler.get_stats()
        stats_parts.append(f"LLM: {stats['llm_calls']}")
        stats_parts.append(f"Tools: {stats['tool_calls']}")

        # Token display with graceful fallback
        if stats["tokens_in"] > 0 or stats["tokens_out"] > 0:
            tokens_str = f"Tokens: {format_tokens(stats['tokens_in'])}\u2191 {format_tokens(stats['tokens_out'])}\u2193"
        else:
            tokens_str = "Tokens: --"
        stats_parts.append(tokens_str)

    stats_parts.append(f"Reports: {reports_completed}/{reports_total}")

    # Elapsed time
    if start_time:
        elapsed = time.time() - start_time
        elapsed_str = f"\u23f1 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
        stats_parts.append(elapsed_str)

    stats_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    stats_table.add_column("Stats", justify="center")
    stats_table.add_row(" | ".join(stats_parts))

    layout["footer"].update(Panel(stats_table, border_style="grey50"))


def get_user_selections():
    """Get all user selections before starting the analysis display."""
    # Display ASCII art welcome message
    with open(Path(__file__).parent / "static" / "welcome.txt", "r") as f:
        welcome_ascii = f.read()

    # Create welcome box content
    welcome_content = f"{welcome_ascii}\n"
    welcome_content += "[bold green]TradingAgents: Multi-Agents LLM Financial Trading Framework - CLI[/bold green]\n\n"
    welcome_content += "[bold]Workflow Steps:[/bold]\n"
    welcome_content += "I. Analyst Team → II. Research Team → III. Trader → IV. Risk Management → V. Portfolio Management\n\n"
    welcome_content += (
        "[dim]Built by [Tauric Research](https://github.com/TauricResearch)[/dim]"
    )

    # Create and center the welcome box
    welcome_box = Panel(
        welcome_content,
        border_style="green",
        padding=(1, 2),
        title="Welcome to TradingAgents",
        subtitle="Multi-Agents LLM Financial Trading Framework",
    )
    console.print(Align.center(welcome_box))
    console.print()
    console.print()  # Add vertical space before announcements

    # Fetch and display announcements (silent on failure)
    announcements = fetch_announcements()
    display_announcements(console, announcements)

    # Create a boxed questionnaire for each step
    def create_question_box(title, prompt, default=None):
        box_content = f"[bold]{title}[/bold]\n"
        box_content += f"[dim]{prompt}[/dim]"
        if default:
            box_content += f"\n[dim]Default: {default}[/dim]"
        return Panel(box_content, border_style="blue", padding=(1, 2))

    # Step 1: Instrument type
    console.print(
        create_question_box(
            "Step 1: Instrument Type",
            "Select the market/instrument class for this analysis",
            "equity",
        )
    )
    selected_instrument_type = select_instrument_type()

    # Step 2: Ticker symbol
    console.print(
        create_question_box(
            "Step 2: Ticker Symbol",
            "Enter the exact ticker symbol to analyze, including exchange suffix when needed (examples: SPY, CNC.TO, 7203.T, 0700.HK)",
            "SPY",
        )
    )
    selected_ticker = get_ticker()

    # Step 3: Analysis date
    default_date = datetime.datetime.now().strftime("%Y-%m-%d")
    console.print(
        create_question_box(
            "Step 3: Analysis Date",
            "Enter the analysis date (YYYY-MM-DD)",
            default_date,
        )
    )
    analysis_date = get_analysis_date()

    # Step 4: Select analysts
    console.print(
        create_question_box(
            "Step 4: Analysts Team", "Select your LLM analyst agents for the analysis"
        )
    )
    selected_analysts = select_analysts()
    console.print(
        f"[green]Selected analysts:[/green] {', '.join(analyst.value for analyst in selected_analysts)}"
    )

    # Step 5: Research depth
    console.print(
        create_question_box(
            "Step 5: Research Depth", "Select your research depth level"
        )
    )
    selected_research_depth = select_research_depth()

    # Step 6: OpenAI backend
    console.print(
        create_question_box(
            "Step 6: OpenAI backend", "Select which service to talk to"
        )
    )
    selected_llm_provider, backend_url = select_llm_provider()
    
    # Step 7: Thinking agents
    console.print(
        create_question_box(
            "Step 7: Thinking Agents", "Select your thinking agents for analysis"
        )
    )
    selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
    selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    # Step 8: Provider-specific thinking configuration
    thinking_level = None
    reasoning_effort = None
    anthropic_effort = None

    provider_lower = selected_llm_provider.lower()
    if provider_lower == "google":
        console.print(
            create_question_box(
                "Step 8: Thinking Mode",
                "Configure Gemini thinking mode"
            )
        )
        thinking_level = ask_gemini_thinking_config()
    elif provider_lower == "openai":
        console.print(
            create_question_box(
                "Step 8: Reasoning Effort",
                "Configure OpenAI reasoning effort level"
            )
        )
        reasoning_effort = ask_openai_reasoning_effort()
    elif provider_lower == "anthropic":
        console.print(
            create_question_box(
                "Step 8: Effort Level",
                "Configure Claude effort level"
            )
        )
        anthropic_effort = ask_anthropic_effort()

    return {
        "instrument_type": selected_instrument_type,
        "ticker": selected_ticker,
        "analysis_date": analysis_date,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
        "google_thinking_level": thinking_level,
        "openai_reasoning_effort": reasoning_effort,
        "anthropic_effort": anthropic_effort,
    }


def get_ticker():
    """Get ticker symbol from user input."""
    return typer.prompt("", default="SPY")


def get_analysis_date():
    """Get the analysis date from user input."""
    while True:
        date_str = typer.prompt(
            "", default=datetime.datetime.now().strftime("%Y-%m-%d")
        )
        try:
            # Validate date format and ensure it's not in the future
            analysis_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if analysis_date.date() > datetime.datetime.now().date():
                console.print("[red]Error: Analysis date cannot be in the future[/red]")
                continue
            return date_str
        except ValueError:
            console.print(
                "[red]Error: Invalid date format. Please use YYYY-MM-DD[/red]"
            )


def save_report_to_disk(final_state, ticker: str, save_path: Path):
    """Save complete analysis report to disk with organized subfolders."""
    import json

    save_path.mkdir(parents=True, exist_ok=True)
    sections = []

    # 1. Analysts
    analysts_dir = save_path / "1_analysts"
    analyst_parts = []
    if final_state.get("market_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "market.md").write_text(final_state["market_report"])
        analyst_parts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "sentiment.md").write_text(final_state["sentiment_report"])
        analyst_parts.append(("Social Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "news.md").write_text(final_state["news_report"])
        analyst_parts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "fundamentals.md").write_text(final_state["fundamentals_report"])
        analyst_parts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analyst_parts:
        content = "\n\n".join(f"### {name}\n{text}" for name, text in analyst_parts)
        sections.append(f"## I. Analyst Team Reports\n\n{content}")

    # 2. Research
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_research"
        debate = final_state["investment_debate_state"]
        research_parts = []
        if debate.get("bull_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bull.md").write_text(debate["bull_history"])
            research_parts.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bear.md").write_text(debate["bear_history"])
            research_parts.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "manager.md").write_text(debate["judge_decision"])
            research_parts.append(("Research Manager", debate["judge_decision"]))
        if research_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in research_parts)
            sections.append(f"## II. Research Team Decision\n\n{content}")

    # 3. Trading
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_trading"
        trading_dir.mkdir(exist_ok=True)
        (trading_dir / "trader.md").write_text(final_state["trader_investment_plan"])
        sections.append(f"## III. Trading Team Plan\n\n### Trader\n{final_state['trader_investment_plan']}")

    # 4. Risk Management
    if final_state.get("risk_debate_state"):
        risk_dir = save_path / "4_risk"
        risk = final_state["risk_debate_state"]
        risk_parts = []
        if risk.get("aggressive_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "aggressive.md").write_text(risk["aggressive_history"])
            risk_parts.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "conservative.md").write_text(risk["conservative_history"])
            risk_parts.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "neutral.md").write_text(risk["neutral_history"])
            risk_parts.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in risk_parts)
            sections.append(f"## IV. Risk Management Team Decision\n\n{content}")

        # 5. Portfolio Manager
        if risk.get("judge_decision"):
            portfolio_dir = save_path / "5_portfolio"
            portfolio_dir.mkdir(exist_ok=True)
            (portfolio_dir / "decision.md").write_text(risk["judge_decision"])
            sections.append(f"## V. Portfolio Manager Decision\n\n### Portfolio Manager\n{risk['judge_decision']}")

    # Write consolidated report
    header = f"# Trading Analysis Report: {ticker}\n\nGenerated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    (save_path / "complete_report.md").write_text(header + "\n\n".join(sections))

    # Persist structured decision payload for automation/audit pipelines.
    if final_state.get("order_intent"):
        with open(save_path / "order_intent.json", "w", encoding="utf-8") as f:
            json.dump(final_state["order_intent"], f, indent=2)

    return save_path / "complete_report.md"


def display_complete_report(final_state):
    """Display the complete analysis report sequentially (avoids truncation)."""
    console.print()
    console.print(Rule("Complete Analysis Report", style="bold green"))

    # I. Analyst Team Reports
    analysts = []
    if final_state.get("market_report"):
        analysts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts.append(("Social Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analysts:
        console.print(Panel("[bold]I. Analyst Team Reports[/bold]", border_style="cyan"))
        for title, content in analysts:
            console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # II. Research Team Reports
    if final_state.get("investment_debate_state"):
        debate = final_state["investment_debate_state"]
        research = []
        if debate.get("bull_history"):
            research.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research.append(("Research Manager", debate["judge_decision"]))
        if research:
            console.print(Panel("[bold]II. Research Team Decision[/bold]", border_style="magenta"))
            for title, content in research:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # III. Trading Team
    if final_state.get("trader_investment_plan"):
        console.print(Panel("[bold]III. Trading Team Plan[/bold]", border_style="yellow"))
        console.print(Panel(Markdown(final_state["trader_investment_plan"]), title="Trader", border_style="blue", padding=(1, 2)))

    # IV. Risk Management Team
    if final_state.get("risk_debate_state"):
        risk = final_state["risk_debate_state"]
        risk_reports = []
        if risk.get("aggressive_history"):
            risk_reports.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_reports.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_reports.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_reports:
            console.print(Panel("[bold]IV. Risk Management Team Decision[/bold]", border_style="red"))
            for title, content in risk_reports:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

        # V. Portfolio Manager Decision
        if risk.get("judge_decision"):
            console.print(Panel("[bold]V. Portfolio Manager Decision[/bold]", border_style="green"))
            console.print(Panel(Markdown(risk["judge_decision"]), title="Portfolio Manager", border_style="blue", padding=(1, 2)))


def update_research_team_status(status):
    """Update status for research team members (not Trader)."""
    research_team = ["Bull Researcher", "Bear Researcher", "Research Manager"]
    for agent in research_team:
        message_buffer.update_agent_status(agent, status)


# Ordered list of analysts for status transitions
ANALYST_ORDER = ["market", "social", "news", "fundamentals"]
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}


def update_analyst_statuses(message_buffer, chunk):
    """Update analyst statuses based on accumulated report state.

    Logic:
    - Store new report content from the current chunk if present
    - Check accumulated report_sections (not just current chunk) for status
    - Analysts with reports = completed
    - First analyst without report = in_progress
    - Remaining analysts without reports = pending
    - When all analysts done, set Bull Researcher to in_progress
    """
    selected = message_buffer.selected_analysts
    found_active = False

    for analyst_key in ANALYST_ORDER:
        if analyst_key not in selected:
            continue

        agent_name = ANALYST_AGENT_NAMES[analyst_key]
        report_key = ANALYST_REPORT_MAP[analyst_key]

        # Capture new report content from current chunk
        if chunk.get(report_key):
            message_buffer.update_report_section(report_key, chunk[report_key])

        # Determine status from accumulated sections, not just current chunk
        has_report = bool(message_buffer.report_sections.get(report_key))

        if has_report:
            message_buffer.update_agent_status(agent_name, "completed")
        elif not found_active:
            message_buffer.update_agent_status(agent_name, "in_progress")
            found_active = True
        else:
            message_buffer.update_agent_status(agent_name, "pending")

    # When all analysts complete, transition research team to in_progress
    if not found_active and selected:
        if message_buffer.agent_status.get("Bull Researcher") == "pending":
            message_buffer.update_agent_status("Bull Researcher", "in_progress")

def extract_content_string(content):
    """Extract string content from various message formats.
    Returns None if no meaningful text content is found.
    """
    import ast

    def is_empty(val):
        """Check if value is empty using Python's truthiness."""
        if val is None or val == '':
            return True
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return True
            try:
                return not bool(ast.literal_eval(s))
            except (ValueError, SyntaxError):
                return False  # Can't parse = real text
        return not bool(val)

    if is_empty(content):
        return None

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        text = content.get('text', '')
        return text.strip() if not is_empty(text) else None

    if isinstance(content, list):
        text_parts = [
            item.get('text', '').strip() if isinstance(item, dict) and item.get('type') == 'text'
            else (item.strip() if isinstance(item, str) else '')
            for item in content
        ]
        result = ' '.join(t for t in text_parts if t and not is_empty(t))
        return result if result else None

    return str(content).strip() if not is_empty(content) else None


def classify_message_type(message) -> tuple[str, str | None]:
    """Classify LangChain message into display type and extract content.

    Returns:
        (type, content) - type is one of: User, Agent, Data, Control
                        - content is extracted string or None
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    content = extract_content_string(getattr(message, 'content', None))

    if isinstance(message, HumanMessage):
        if content and content.strip() == "Continue":
            return ("Control", content)
        return ("User", content)

    if isinstance(message, ToolMessage):
        return ("Data", content)

    if isinstance(message, AIMessage):
        return ("Agent", content)

    # Fallback for unknown types
    return ("System", content)


def format_tool_args(args, max_length=80) -> str:
    """Format tool arguments for terminal display."""
    result = str(args)
    if len(result) > max_length:
        return result[:max_length - 3] + "..."
    return result


def _normalize_symbol_for_path(symbol: str) -> str:
    return symbol.replace("/", "_").replace("-", "_")


def load_strategy_playbook(symbol: str) -> Optional[Dict[str, Any]]:
    symbol_key = _normalize_symbol_for_path(symbol)
    base_dir = Path.cwd() / "strategy_lab_results" / symbol_key

    playbook_file = base_dir / f"strategy_playbook_{symbol_key}.json"
    if playbook_file.exists():
        try:
            return json.loads(playbook_file.read_text(encoding="utf-8"))
        except Exception:
            return None

    summary_file = base_dir / f"strategy_lab_{symbol_key}.json"
    if summary_file.exists():
        try:
            payload = json.loads(summary_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("playbook"):
                return payload["playbook"]
        except Exception:
            return None

    return None


def get_next_governance_run_index(previous_playbook: Optional[Dict[str, Any]]) -> int:
    if not isinstance(previous_playbook, dict):
        return 1

    governance = previous_playbook.get("governance", {})
    if not isinstance(governance, dict):
        return 1

    try:
        last = int(governance.get("lifecycle_run_index", 0))
    except (TypeError, ValueError):
        last = 0
    return max(0, last) + 1


def load_autolab_run_snapshots(symbol: str, limit: int = 10) -> list[Dict[str, Any]]:
    if limit <= 0:
        return []

    symbol_key = _normalize_symbol_for_path(symbol)
    run_log_file = Path.cwd() / "strategy_lab_results" / symbol_key / "autolab_runs.jsonl"
    if not run_log_file.exists():
        return []

    rows: list[Dict[str, Any]] = []
    for raw in run_log_file.read_text(encoding="utf-8").splitlines():
        text = raw.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)

    return rows[-limit:]


def build_governance_report(symbol: str, runs_limit: int = 10) -> Dict[str, Any]:
    symbol_key = _normalize_symbol_for_path(symbol)
    base_dir = Path.cwd() / "strategy_lab_results" / symbol_key
    playbook_file = base_dir / f"strategy_playbook_{symbol_key}.json"
    run_log_file = base_dir / "autolab_runs.jsonl"

    playbook = load_strategy_playbook(symbol)
    governance = playbook.get("governance", {}) if isinstance(playbook, dict) else {}
    if not isinstance(governance, dict):
        governance = {}

    promoted_strategy = playbook.get("promoted_strategy", {}) if isinstance(playbook, dict) else {}
    if not isinstance(promoted_strategy, dict):
        promoted_strategy = {}

    recent_rows = load_autolab_run_snapshots(symbol=symbol, limit=runs_limit)
    recent_runs: list[Dict[str, Any]] = []
    for row in recent_rows:
        promoted = row.get("promoted_strategy", {}) if isinstance(row.get("promoted_strategy"), dict) else {}
        recent_runs.append(
            {
                "run_index": row.get("run_index"),
                "lifecycle_run_index": row.get("lifecycle_run_index"),
                "run_at": row.get("run_at"),
                "promotion_status": row.get("promotion_status"),
                "promoted_strategy": promoted.get("name", ""),
                "score": row.get("score"),
                "passed_filters": row.get("passed_filters"),
            }
        )

    playbook_found = isinstance(playbook, dict) and bool(playbook)
    run_log_found = run_log_file.exists()
    artifacts_found = playbook_found or run_log_found

    return {
        "symbol": symbol,
        "symbol_key": symbol_key,
        "base_dir": str(base_dir),
        "playbook_file": str(playbook_file),
        "run_log_file": str(run_log_file),
        "playbook_found": playbook_found,
        "run_log_found": run_log_found,
        "artifacts_found": artifacts_found,
        "promotion_status": playbook.get("promotion_status", "unknown") if playbook_found else "unknown",
        "promoted_strategy": promoted_strategy,
        "governance": governance,
        "recent_runs_count": len(recent_runs),
        "recent_runs": recent_runs,
        "latest_run": recent_runs[-1] if recent_runs else None,
    }


def build_ops_report(
    symbol: str,
    *,
    journal_limit: int = 10,
    day_utc: str | None = None,
    limit_scan_lines: int = 100_000,
    governance_runs_limit: int = 10,
) -> Dict[str, Any]:
    wallet = get_paper_wallet_snapshot()
    today_exec = count_today_executions(statuses={"simulated_filled", "submitted"})
    recent_rows = read_journal_tail(limit=journal_limit)
    daily = get_daily_summary(day_utc=day_utc, limit_scan_lines=limit_scan_lines)
    governance = build_governance_report(symbol=symbol, runs_limit=governance_runs_limit)

    return {
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "runtime_status": {
            "today_executions": today_exec,
            "paper_wallet": wallet,
            "recent_journal": recent_rows,
        },
        "daily_summary": daily,
        "governance_report": governance,
    }


def render_ops_report_table(report: Dict[str, Any]) -> None:
    runtime = report.get("runtime_status", {}) if isinstance(report.get("runtime_status"), dict) else {}
    daily = report.get("daily_summary", {}) if isinstance(report.get("daily_summary"), dict) else {}
    governance_report = report.get("governance_report", {}) if isinstance(report.get("governance_report"), dict) else {}
    governance = governance_report.get("governance", {}) if isinstance(governance_report.get("governance"), dict) else {}
    drift = governance.get("execution_drift", {}) if isinstance(governance.get("execution_drift"), dict) else {}

    console.print("\n[bold cyan]Operations Report[/bold cyan]")

    summary = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
    summary.add_column("Field", style="cyan")
    summary.add_column("Value", style="green")
    summary.add_row("Generated (UTC)", str(report.get("generated_at_utc", "N/A")))
    summary.add_row("Symbol", str(governance_report.get("symbol", "N/A")))
    summary.add_row("Promotion Status", str(governance_report.get("promotion_status", "unknown")))
    summary.add_row("Lifecycle Run Index", str(governance.get("lifecycle_run_index", "N/A")))
    summary.add_row("Cooldown Active", "yes" if governance.get("cooldown_active") else "no")
    summary.add_row("Today Executions", str(runtime.get("today_executions", 0)))
    summary.add_row("Daily Rows", str(daily.get("total_rows", 0)))
    summary.add_row("Daily Executed", str(daily.get("executed_count", 0)))
    summary.add_row("Daily Rejected", str(daily.get("rejected_count", 0)))
    summary.add_row("Daily Blocked", str(daily.get("blocked_count", 0)))
    summary.add_row("Drift Samples", str(drift.get("sample_count", 0)))
    summary.add_row("Drift Fill Rate", f"{float(drift.get('fill_rate', 0.0)):.2f}")
    summary.add_row("Drift Rejection Ratio", f"{float(drift.get('rejection_ratio', 0.0)):.2f}")
    summary.add_row("Drift Blocked Ratio", f"{float(drift.get('blocked_ratio', 0.0)):.2f}")
    console.print(summary)

    recent_runs = governance_report.get("recent_runs", []) if isinstance(governance_report.get("recent_runs"), list) else []
    if recent_runs:
        runs_table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
        runs_table.add_column("Run", style="cyan")
        runs_table.add_column("Lifecycle", style="green")
        runs_table.add_column("Status", style="yellow")
        runs_table.add_column("Strategy", style="white")
        runs_table.add_column("Score", style="green")
        for row in recent_runs:
            if not isinstance(row, dict):
                continue
            runs_table.add_row(
                str(row.get("run_index", "N/A")),
                str(row.get("lifecycle_run_index", "N/A")),
                str(row.get("promotion_status", "N/A")),
                str(row.get("promoted_strategy", "")) or "-",
                f"{float(row.get('score', 0.0)):.4f}" if row.get("score") is not None else "N/A",
            )
        console.print("\n[bold]Recent Governance Runs[/bold]")
        console.print(runs_table)

    wallet = runtime.get("paper_wallet", {}) if isinstance(runtime.get("paper_wallet"), dict) else {}
    wallet_summary = wallet.get("summary", {}) if isinstance(wallet.get("summary"), dict) else {}
    wallet_table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
    wallet_table.add_column("Wallet Metric", style="cyan")
    wallet_table.add_column("Value", style="green")
    for key in ["cash", "equity", "unrealized_pnl", "open_positions", "orders_count"]:
        wallet_table.add_row(key, str(wallet_summary.get(key, "N/A")))
    console.print("\n[bold]Paper Wallet[/bold]")
    console.print(wallet_table)


def find_latest_order_intent_file(reports_root: Path, ticker: str = "") -> Optional[Path]:
    if not reports_root.exists():
        return None

    candidates = list(reports_root.rglob("order_intent.json"))
    if ticker:
        ticker_up = ticker.upper()
        candidates = [p for p in candidates if ticker_up in p.as_posix().upper()]
    if not candidates:
        return None

    return max(candidates, key=lambda p: p.stat().st_mtime)

def run_analysis():
    # First get all user selections
    selections = get_user_selections()

    # Create config with selected research depth
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = selections["research_depth"]
    config["max_risk_discuss_rounds"] = selections["research_depth"]
    config["quick_think_llm"] = selections["shallow_thinker"]
    config["deep_think_llm"] = selections["deep_thinker"]
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"].lower()
    config["instrument_type"] = selections.get("instrument_type", "equity")
    strategy_playbook = load_strategy_playbook(selections["ticker"])
    config["instrument_metadata"] = {}
    if strategy_playbook:
        config["instrument_metadata"]["strategy_playbook"] = strategy_playbook
    # Provider-specific thinking configuration
    config["google_thinking_level"] = selections.get("google_thinking_level")
    config["openai_reasoning_effort"] = selections.get("openai_reasoning_effort")
    config["anthropic_effort"] = selections.get("anthropic_effort")

    # Create stats callback handler for tracking LLM/tool calls
    stats_handler = StatsCallbackHandler()

    # Normalize analyst selection to predefined order (selection is a 'set', order is fixed)
    selected_set = {analyst.value for analyst in selections["analysts"]}
    selected_analyst_keys = [a for a in ANALYST_ORDER if a in selected_set]

    # Initialize the graph with callbacks bound to LLMs
    graph = TradingAgentsGraph(
        selected_analyst_keys,
        config=config,
        debug=True,
        callbacks=[stats_handler],
    )

    # Initialize message buffer with selected analysts
    message_buffer.init_for_analysis(selected_analyst_keys)

    # Track start time for elapsed display
    start_time = time.time()

    # Create result directory
    results_dir = Path(config["results_dir"]) / selections["ticker"] / selections["analysis_date"]
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = results_dir / "message_tool.log"
    log_file.touch(exist_ok=True)

    def save_message_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, message_type, content = obj.messages[-1]
            content = content.replace("\n", " ")  # Replace newlines with spaces
            with open(log_file, "a") as f:
                f.write(f"{timestamp} [{message_type}] {content}\n")
        return wrapper
    
    def save_tool_call_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, tool_name, args = obj.tool_calls[-1]
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            with open(log_file, "a") as f:
                f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")
        return wrapper

    def save_report_section_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(section_name, content):
            func(section_name, content)
            if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
                content = obj.report_sections[section_name]
                if content:
                    file_name = f"{section_name}.md"
                    text = "\n".join(str(item) for item in content) if isinstance(content, list) else content
                    with open(report_dir / file_name, "w") as f:
                        f.write(text)
        return wrapper

    message_buffer.add_message = save_message_decorator(message_buffer, "add_message")
    message_buffer.add_tool_call = save_tool_call_decorator(message_buffer, "add_tool_call")
    message_buffer.update_report_section = save_report_section_decorator(message_buffer, "update_report_section")

    # Now start the display layout
    layout = create_layout()

    shutdown_manager = GracefulShutdownManager()
    shutdown_manager.setup_signal_handlers()

    try:
        with Live(layout, refresh_per_second=4) as live:
            # Initial display
            update_display(layout, stats_handler=stats_handler, start_time=start_time)

            # Add initial messages
            message_buffer.add_message("System", f"Selected ticker: {selections['ticker']}")
            message_buffer.add_message(
                "System", f"Instrument type: {selections.get('instrument_type', 'equity')}"
            )
            if strategy_playbook:
                promoted = strategy_playbook.get("promoted_strategy", {})
                promotion_status = str(strategy_playbook.get("promotion_status", "")).strip().lower()
                best = promoted if promotion_status == "promoted" and promoted else strategy_playbook.get("best_strategy", {})
                message_buffer.add_message(
                    "System",
                    f"Loaded strategy playbook: {best.get('name', 'N/A')} ({best.get('family', 'N/A')})",
                )
            message_buffer.add_message(
                "System", f"Analysis date: {selections['analysis_date']}"
            )
            message_buffer.add_message(
                "System",
                f"Selected analysts: {', '.join(analyst.value for analyst in selections['analysts'])}",
            )
            update_display(layout, stats_handler=stats_handler, start_time=start_time)

            # Update agent status to in_progress for the first analyst
            first_analyst = f"{selections['analysts'][0].value.capitalize()} Analyst"
            message_buffer.update_agent_status(first_analyst, "in_progress")
            update_display(layout, stats_handler=stats_handler, start_time=start_time)

            # Create spinner text
            spinner_text = (
                f"Analyzing {selections['ticker']} on {selections['analysis_date']}..."
            )
            update_display(layout, spinner_text, stats_handler=stats_handler, start_time=start_time)

            # Initialize state and get graph args with callbacks
            init_agent_state = graph.propagator.create_initial_state(
                selections["ticker"],
                selections["analysis_date"],
                instrument_type=selections.get("instrument_type", "equity"),
                instrument_metadata=config.get("instrument_metadata", {}),
            )
            # Pass callbacks to graph config for tool execution tracking
            # (LLM tracking is handled separately via LLM constructor)
            args = graph.propagator.get_graph_args(callbacks=[stats_handler])

            # Stream the analysis
            trace = []
            for chunk in graph.graph.stream(init_agent_state, **args):
                if shutdown_manager.shutdown_requested:
                    message_buffer.add_message("System", "Shutdown requested. Stopping analysis gracefully.")
                    update_display(layout, stats_handler=stats_handler, start_time=start_time)
                    break
                # Process messages if present (skip duplicates via message ID)
                if len(chunk["messages"]) > 0:
                    last_message = chunk["messages"][-1]
                    msg_id = getattr(last_message, "id", None)

                    if msg_id != message_buffer._last_message_id:
                        message_buffer._last_message_id = msg_id

                        # Add message to buffer
                        msg_type, content = classify_message_type(last_message)
                        if content and content.strip():
                            message_buffer.add_message(msg_type, content)

                        # Handle tool calls
                        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                            for tool_call in last_message.tool_calls:
                                if isinstance(tool_call, dict):
                                    message_buffer.add_tool_call(
                                        tool_call["name"], tool_call["args"]
                                    )
                                else:
                                    message_buffer.add_tool_call(tool_call.name, tool_call.args)

                # Update analyst statuses based on report state (runs on every chunk)
                update_analyst_statuses(message_buffer, chunk)

                # Research Team - Handle Investment Debate State
                if chunk.get("investment_debate_state"):
                    debate_state = chunk["investment_debate_state"]
                    bull_hist = debate_state.get("bull_history", "").strip()
                    bear_hist = debate_state.get("bear_history", "").strip()
                    judge = debate_state.get("judge_decision", "").strip()

                    # Only update status when there's actual content
                    if bull_hist or bear_hist:
                        update_research_team_status("in_progress")
                    if bull_hist:
                        message_buffer.update_report_section(
                            "investment_plan", f"### Bull Researcher Analysis\n{bull_hist}"
                        )
                    if bear_hist:
                        message_buffer.update_report_section(
                            "investment_plan", f"### Bear Researcher Analysis\n{bear_hist}"
                        )
                    if judge:
                        message_buffer.update_report_section(
                            "investment_plan", f"### Research Manager Decision\n{judge}"
                        )
                        update_research_team_status("completed")
                        message_buffer.update_agent_status("Trader", "in_progress")

                # Trading Team
                if chunk.get("trader_investment_plan"):
                    message_buffer.update_report_section(
                        "trader_investment_plan", chunk["trader_investment_plan"]
                    )
                    if message_buffer.agent_status.get("Trader") != "completed":
                        message_buffer.update_agent_status("Trader", "completed")
                        message_buffer.update_agent_status("Aggressive Analyst", "in_progress")

                # Risk Management Team - Handle Risk Debate State
                if chunk.get("risk_debate_state"):
                    risk_state = chunk["risk_debate_state"]
                    agg_hist = risk_state.get("aggressive_history", "").strip()
                    con_hist = risk_state.get("conservative_history", "").strip()
                    neu_hist = risk_state.get("neutral_history", "").strip()
                    judge = risk_state.get("judge_decision", "").strip()

                    if agg_hist:
                        if message_buffer.agent_status.get("Aggressive Analyst") != "completed":
                            message_buffer.update_agent_status("Aggressive Analyst", "in_progress")
                        message_buffer.update_report_section(
                            "final_trade_decision", f"### Aggressive Analyst Analysis\n{agg_hist}"
                        )
                    if con_hist:
                        if message_buffer.agent_status.get("Conservative Analyst") != "completed":
                            message_buffer.update_agent_status("Conservative Analyst", "in_progress")
                        message_buffer.update_report_section(
                            "final_trade_decision", f"### Conservative Analyst Analysis\n{con_hist}"
                        )
                    if neu_hist:
                        if message_buffer.agent_status.get("Neutral Analyst") != "completed":
                            message_buffer.update_agent_status("Neutral Analyst", "in_progress")
                        message_buffer.update_report_section(
                            "final_trade_decision", f"### Neutral Analyst Analysis\n{neu_hist}"
                        )
                    if judge:
                        if message_buffer.agent_status.get("Portfolio Manager") != "completed":
                            message_buffer.update_agent_status("Portfolio Manager", "in_progress")
                            message_buffer.update_report_section(
                                "final_trade_decision", f"### Portfolio Manager Decision\n{judge}"
                            )
                            message_buffer.update_agent_status("Aggressive Analyst", "completed")
                            message_buffer.update_agent_status("Conservative Analyst", "completed")
                            message_buffer.update_agent_status("Neutral Analyst", "completed")
                            message_buffer.update_agent_status("Portfolio Manager", "completed")

                # Update the display
                update_display(layout, stats_handler=stats_handler, start_time=start_time)

                trace.append(chunk)

            # Get final state and decision
            if not trace:
                raise typer.Exit(code=1)
            final_state = trace[-1]
            order_intent = graph.signal_processor.extract_order_intent(
                full_signal=final_state.get("final_trade_decision", ""),
                trader_plan=final_state.get("trader_investment_plan", ""),
                ticker=selections["ticker"],
                instrument_type=selections.get("instrument_type", "equity"),
                analyst_teams=selected_analyst_keys,
                debate_rounds_used=final_state.get("risk_debate_state", {}).get("count", 0),
                research_depth=str(selections.get("research_depth", "unknown")),
                final_state=final_state,
            )
            final_state["order_intent"] = order_intent.model_dump()
            decision = order_intent.signal

            # Update all agent statuses to completed
            for agent in message_buffer.agent_status:
                message_buffer.update_agent_status(agent, "completed")

            message_buffer.add_message(
                "System", f"Completed analysis for {selections['analysis_date']}"
            )
            message_buffer.add_message(
                "System",
                f"Order Intent: {order_intent.signal} (confidence={order_intent.confidence:.2f}, consistency={order_intent.consistency_score:.2f})",
            )
            if order_intent.validation_warnings:
                message_buffer.add_message(
                    "System",
                    "Warnings: " + " | ".join(order_intent.validation_warnings),
                )

            # Update final report sections
            for section in message_buffer.report_sections.keys():
                if section in final_state:
                    message_buffer.update_report_section(section, final_state[section])

            update_display(layout, stats_handler=stats_handler, start_time=start_time)
    finally:
        shutdown_manager.restore_signal_handlers()

    # Post-analysis prompts (outside Live context for clean interaction)
    console.print("\n[bold cyan]Analysis Complete![/bold cyan]\n")

    # Prompt to save report
    save_choice = typer.prompt("Save report?", default="Y").strip().upper()
    if save_choice in ("Y", "YES", ""):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_path = Path.cwd() / "reports" / f"{selections['ticker']}_{timestamp}"
        save_path_str = typer.prompt(
            "Save path (press Enter for default)",
            default=str(default_path)
        ).strip()
        save_path = Path(save_path_str)
        try:
            report_file = save_report_to_disk(final_state, selections["ticker"], save_path)
            console.print(f"\n[green]✓ Report saved to:[/green] {save_path.resolve()}")
            console.print(f"  [dim]Complete report:[/dim] {report_file.name}")
        except Exception as e:
            console.print(f"[red]Error saving report: {e}[/red]")

    # Prompt to display full report
    display_choice = typer.prompt("\nDisplay full report on screen?", default="Y").strip().upper()
    if display_choice in ("Y", "YES", ""):
        display_complete_report(final_state)


@app.command()
def analyze():
    run_analysis()

@app.command()
def daemon(
    symbols: str = typer.Option("BTC-USD,NVDA", help="Comma-separated list of symbols to run analysis on."),
    cron: str = typer.Option("0 * * * *", help="Cron expression for the analysis schedule (e.g. '0 * * * *' for hourly)."),
    analysts: str = typer.Option("market,news,fundamentals", help="Comma-separated analysts."),
    debug: bool = typer.Option(False, help="Run in debug mode")
):
    """Start the 24x7 VPS daemon for autonomous analysis and trade intents generation."""
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    analysts_list = [a.strip() for a in analysts.split(",") if a.strip()]
    console.print(f"[bold cyan]Starting Daemon[/bold cyan] for {symbol_list} on cron schedule: '{cron}'")
    start_daemon(symbol_list, cron, analysts_list, debug)


@app.command()
def quant_lab(
    symbol: str = typer.Option("BTC-USD", help="Symbol to generate strategy backtest for."),
    market_context: str = typer.Option("Trending upwards, low volatility.", help="Context seed"),
):
    """Generates an LLM vectorbt script, executes it, and logs the result."""
    from tradingagents.strategy_lab.quant_orchestrator import run_quant_cycle
    from tradingagents.llm_clients import create_llm_client
    
    config = DEFAULT_CONFIG
    client = create_llm_client(
        provider=config["llm_provider"],
        model=config["deep_think_llm"],
        base_url=config.get("backend_url"),
    )
    llm = client.get_llm()
    console.print(f"[bold cyan]Injecting LLM logic for VectorBT strategies...[/bold cyan]")
    run_quant_cycle(llm, symbol, market_context)

@app.command()
def autolab(
    symbol: str = typer.Option("BTC-USD", help="Symbol to evolve strategies on"),
    instrument_type: str = typer.Option("crypto", help="Instrument type: equity, forex, crypto, options"),
    cycles: int = typer.Option(5, min=1, help="Evolution cycles"),
    population: int = typer.Option(12, min=4, help="Strategies per cycle"),
    elites: int = typer.Option(4, min=1, help="Top strategies to mutate each cycle"),
    period: str = typer.Option("2y", help="yfinance period, e.g., 1y, 2y, 5y"),
    interval: str = typer.Option("1d", help="yfinance interval, e.g., 1d, 4h, 1h"),
    fee_bps: float = typer.Option(5.0, help="Estimated transaction cost in basis points"),
    min_trades: int = typer.Option(8, min=1, help="Minimum trade count robustness gate"),
    max_out_drawdown: float = typer.Option(0.35, min=0.01, max=0.95, help="Maximum out-of-sample drawdown"),
    min_out_sharpe: float = typer.Option(-0.25, help="Minimum out-of-sample Sharpe ratio"),
    min_out_return: float = typer.Option(-0.20, help="Minimum out-of-sample return (decimal)"),
    promotion_min_score: float = typer.Option(0.0, help="Minimum composite score to auto-promote"),
    promotion_min_out_sharpe: float = typer.Option(0.25, help="Minimum out-of-sample Sharpe for auto-promotion"),
    promotion_min_out_return: float = typer.Option(0.0, help="Minimum out-of-sample return for auto-promotion"),
    promotion_max_out_drawdown: float = typer.Option(0.25, min=0.01, max=0.95, help="Maximum out-of-sample drawdown for auto-promotion"),
    promotion_min_trades: int = typer.Option(12, min=1, help="Minimum trades required for auto-promotion"),
    promotion_cooldown_runs: int = typer.Option(2, min=0, help="Cooldown runs after promotion before drift-based demotion checks"),
    promotion_drift_lookback_trades: int = typer.Option(30, min=5, help="How many recent trade journal rows to inspect for promotion drift"),
    promotion_drift_min_samples: int = typer.Option(8, min=1, help="Minimum trade samples required before drift governance can demote"),
    promotion_drift_min_fill_rate: float = typer.Option(0.35, min=0.0, max=1.0, help="Minimum fill/submitted rate required to keep promoted strategy"),
    promotion_drift_max_rejection_ratio: float = typer.Option(0.55, min=0.0, max=1.0, help="Maximum rejected ratio allowed before demotion"),
    promotion_drift_max_blocked_ratio: float = typer.Option(0.35, min=0.0, max=1.0, help="Maximum blocked ratio allowed before demotion"),
    loop: bool = typer.Option(False, help="Run repeatedly on a schedule"),
    every_minutes: float = typer.Option(30.0, min=0.1, help="Minutes between scheduled runs"),
    max_runs: int = typer.Option(24, min=1, help="Total runs when --loop is enabled"),
    seed: int = typer.Option(42, help="Random seed for reproducibility"),
):
    """Run autonomous strategy generation and backtesting cycles."""
    if elites > population:
        raise typer.BadParameter("elites cannot be greater than population")

    out_dir = Path.cwd() / "strategy_lab_results" / _normalize_symbol_for_path(symbol)
    run_log_file = out_dir / "autolab_runs.jsonl"
    playbook_file = out_dir / f"strategy_playbook_{_normalize_symbol_for_path(symbol)}.json"
    summary_file = out_dir / f"strategy_lab_{_normalize_symbol_for_path(symbol)}.json"
    run_total = max_runs if loop else 1
    governance_policy = PromotionGovernancePolicy(
        cooldown_runs=promotion_cooldown_runs,
        drift_lookback_trades=promotion_drift_lookback_trades,
        drift_min_samples=promotion_drift_min_samples,
        drift_min_fill_rate=promotion_drift_min_fill_rate,
        drift_max_rejection_ratio=promotion_drift_max_rejection_ratio,
        drift_max_blocked_ratio=promotion_drift_max_blocked_ratio,
    )

    for run_idx in range(1, run_total + 1):
        previous_playbook: Dict[str, Any] = {}
        if playbook_file.exists():
            try:
                previous_playbook = json.loads(playbook_file.read_text(encoding="utf-8"))
            except Exception:
                previous_playbook = {}

        lifecycle_run_index = get_next_governance_run_index(previous_playbook)

        orchestrator = StrategyLabOrchestrator(seed=seed + run_idx - 1)
        summary = orchestrator.run(
            symbol=symbol,
            instrument_type=instrument_type,
            cycles=cycles,
            population_size=population,
            elite_count=elites,
            period=period,
            interval=interval,
            fee_bps=fee_bps,
            min_trades=min_trades,
            max_out_drawdown=max_out_drawdown,
            min_out_sharpe=min_out_sharpe,
            min_out_return=min_out_return,
            promotion_min_score=promotion_min_score,
            promotion_min_out_sharpe=promotion_min_out_sharpe,
            promotion_min_out_return=promotion_min_out_return,
            promotion_max_out_drawdown=promotion_max_out_drawdown,
            promotion_min_trades=promotion_min_trades,
            output_dir=out_dir,
        )

        recent_rows = read_journal_tail(limit=max(10, promotion_drift_lookback_trades * 3))
        governed_playbook = apply_promotion_governance(
            summary.get("playbook", {}),
            previous_playbook=previous_playbook,
            run_index=lifecycle_run_index,
            recent_rows=recent_rows,
            policy=governance_policy,
        )
        summary["playbook"] = governed_playbook
        if summary.get("playbook_file"):
            Path(summary["playbook_file"]).write_text(json.dumps(governed_playbook, indent=2), encoding="utf-8")
        elif playbook_file:
            playbook_file.parent.mkdir(parents=True, exist_ok=True)
            playbook_file.write_text(json.dumps(governed_playbook, indent=2), encoding="utf-8")

        if summary.get("output_file"):
            Path(summary["output_file"]).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        elif summary_file:
            summary_file.parent.mkdir(parents=True, exist_ok=True)
            summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        best = summary.get("best_overall") or {}
        spec = best.get("spec") or {}
        playbook = governed_playbook
        promoted_spec = playbook.get("promoted_strategy") or {}
        promotion_status = playbook.get("promotion_status", "not_promoted")
        governance = playbook.get("governance", {}) if isinstance(playbook.get("governance"), dict) else {}
        execution_drift = governance.get("execution_drift", {}) if isinstance(governance.get("execution_drift"), dict) else {}

        out_dir.mkdir(parents=True, exist_ok=True)
        run_snapshot = {
            "run_index": run_idx,
            "lifecycle_run_index": lifecycle_run_index,
            "run_at": summary.get("run_at"),
            "symbol": summary.get("symbol", symbol),
            "instrument_type": summary.get("instrument_type", instrument_type),
            "best_strategy": spec,
            "score": best.get("score", 0.0),
            "passed_filters": best.get("passed_filters", False),
            "promotion_status": promotion_status,
            "promoted_strategy": promoted_spec,
            "output_file": summary.get("output_file"),
            "playbook_file": summary.get("playbook_file"),
        }
        with open(run_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(run_snapshot) + "\n")

        table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")
        display_run = f"{run_idx}/{run_total}" if loop else "single"
        table.add_row("Run", display_run)
        table.add_row("Lifecycle Run Index", str(lifecycle_run_index))
        table.add_row("Symbol", summary.get("symbol", symbol))
        table.add_row("Instrument Type", summary.get("instrument_type", instrument_type))
        table.add_row("Cycles", str(summary.get("cycles", cycles)))
        table.add_row("Best Strategy", spec.get("name", "N/A"))
        table.add_row("Family", spec.get("family", "N/A"))
        table.add_row("Promotion Status", promotion_status)
        table.add_row("Promoted Strategy", promoted_spec.get("name", "N/A"))
        table.add_row("Promotion Cooldown Active", "yes" if governance.get("cooldown_active") else "no")
        table.add_row("Drift Sample Count", str(execution_drift.get("sample_count", 0)))
        table.add_row("Drift Fill Rate", f"{float(execution_drift.get('fill_rate', 0.0)):.2f}")
        table.add_row("Drift Rejection Ratio", f"{float(execution_drift.get('rejection_ratio', 0.0)):.2f}")
        table.add_row("Drift Blocked Ratio", f"{float(execution_drift.get('blocked_ratio', 0.0)):.2f}")
        table.add_row("Score", f"{best.get('score', 0.0):.4f}")
        table.add_row("Passed Filters", "yes" if best.get("passed_filters", False) else "no")
        table.add_row("Robustness Penalty", f"{best.get('robustness_penalty', 0.0):.4f}")
        table.add_row("Overfit Gap", f"{best.get('overfit_gap', 0.0):.4f}")
        table.add_row("Out Return", f"{best.get('out_sample_return', 0.0) * 100:.2f}%")
        table.add_row("Out Sharpe", f"{best.get('out_sample_sharpe', 0.0):.3f}")
        table.add_row("Out MaxDD", f"{best.get('out_sample_max_drawdown', 0.0) * 100:.2f}%")
        table.add_row("Trades", str(best.get("trades", 0)))
        table.add_row("Result File", summary.get("output_file", "N/A"))
        table.add_row("Playbook File", summary.get("playbook_file", "N/A"))
        table.add_row("Run Log", str(run_log_file))

        if best.get("notes"):
            table.add_row("Notes", " | ".join(best.get("notes", [])))

        console.print("\n[bold cyan]Strategy Lab Complete[/bold cyan]")
        console.print(table)

        if loop and run_idx < run_total:
            sleep_seconds = int(every_minutes * 60)
            console.print(
                f"[yellow]Next run in {sleep_seconds} seconds ({every_minutes:.2f} minutes)...[/yellow]"
            )
            time.sleep(sleep_seconds)


@app.command()
def healthcheck(
    strict: bool = typer.Option(False, help="Exit with non-zero code on warn/fail checks"),
):
    """Run production readiness checks for crypto, forex, and Nifty options paths."""
    results = run_production_healthcheck()

    table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Details", style="white")

    warn_count = 0
    fail_count = 0

    for item in results:
        status = item.status.lower()
        if status == "pass":
            rendered_status = "[green]PASS[/green]"
        elif status == "warn":
            rendered_status = "[yellow]WARN[/yellow]"
            warn_count += 1
        else:
            rendered_status = "[red]FAIL[/red]"
            fail_count += 1
        table.add_row(item.name, rendered_status, item.details)

    console.print("\n[bold cyan]TradingAgents Production Healthcheck[/bold cyan]")
    console.print(table)

    if strict and (warn_count > 0 or fail_count > 0):
        raise typer.Exit(code=1)


@app.command()
def bootstrap(
    sync_dhan_from: str = typer.Option(
        "",
        help="Optional path to Dhan config.json to import credentials for Nifty option-chain",
    ),
    write_env: bool = typer.Option(True, help="Write merged values to local .env"),
    run_healthcheck: bool = typer.Option(True, help="Run healthcheck immediately after bootstrap"),
    strict: bool = typer.Option(False, help="Fail if healthcheck returns warn/fail"),
):
    """Bootstrap production environment variables and optionally run healthcheck."""
    env_file = Path.cwd() / ".env"
    sync_path = Path(sync_dhan_from) if sync_dhan_from else None

    result = bootstrap_environment(
        env_file=env_file,
        sync_dhan_config=sync_path,
        write_env=write_env,
    )

    # Ensure immediate commands in this process (e.g., post-bootstrap healthcheck)
    # see the merged values even when they were sourced from files.
    for key, value in result.merged_values.items():
        if value:
            os.environ[key] = value

    table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
    table.add_column("Key", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Source", style="yellow")

    llm_available = False
    for item in result.items:
        has_value = bool(item.value)
        if item.key in LLM_KEYS and has_value:
            llm_available = True
        status = "[green]SET[/green]" if has_value else "[red]MISSING[/red]"
        table.add_row(item.key, status, item.source)

    console.print("\n[bold cyan]TradingAgents Environment Bootstrap[/bold cyan]")
    console.print(table)

    if result.written_file:
        console.print(f"[green]Updated env file:[/green] {result.written_file}")

    if not llm_available:
        console.print("[red]No LLM provider key configured. Add at least one of OPENAI/GOOGLE/ANTHROPIC/XAI/OPENROUTER.[/red]")

    if run_healthcheck:
        check_results = run_production_healthcheck()
        warn_count = sum(1 for item in check_results if item.status == "warn")
        fail_count = sum(1 for item in check_results if item.status == "fail")

        hc_table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
        hc_table.add_column("Check", style="cyan")
        hc_table.add_column("Status", style="green")
        hc_table.add_column("Details", style="white")

        for item in check_results:
            if item.status == "pass":
                rendered = "[green]PASS[/green]"
            elif item.status == "warn":
                rendered = "[yellow]WARN[/yellow]"
            else:
                rendered = "[red]FAIL[/red]"
            hc_table.add_row(item.name, rendered, item.details)

        console.print("\n[bold cyan]Post-Bootstrap Healthcheck[/bold cyan]")
        console.print(hc_table)

        if strict and (warn_count > 0 or fail_count > 0):
            raise typer.Exit(code=1)


@app.command()
def trade(
    symbol: str = typer.Option("", help="Trading symbol, e.g. NIFTY25SEP24500CE or BTCUSD"),
    instrument_type: str = typer.Option("options", help="options or spot"),
    signal: str = typer.Option("BUY", help="BUY/SELL/HOLD"),
    quantity: float = typer.Option(1.0, min=0.0001, help="Order quantity"),
    intent_file: str = typer.Option("", help="Path to saved order_intent JSON"),
    broker: str = typer.Option("auto", help="auto, dhan, delta"),
    paper: bool = typer.Option(True, help="Paper mode (safe default)"),
    live: bool = typer.Option(False, help="Set --live to place live orders"),
    show_positions: bool = typer.Option(False, help="List open positions for selected broker"),
    show_wallet: bool = typer.Option(False, help="Show local paper wallet snapshot"),
    close_position: bool = typer.Option(False, help="Close an open position for symbol"),
    cancel_all: bool = typer.Option(False, help="Cancel all open orders for broker"),
    security_id: str = typer.Option("", help="Required for Dhan live options orders"),
    product_id: int = typer.Option(0, help="Optional Delta product_id to skip lookup"),
    exchange_segment: str = typer.Option("NSE_FNO", help="Dhan exchange segment"),
    product_type: str = typer.Option("INTRADAY", help="Dhan product type"),
    stop_loss: Optional[float] = typer.Option(None, help="Optional stop-loss for bracket-style order"),
    target: Optional[float] = typer.Option(None, help="Optional target for bracket-style order"),
    trailing_jump: float = typer.Option(0.0, min=0.0, help="Optional trailing jump for options"),
    net_quantity: float = typer.Option(0.0, help="Required to close Dhan positions if close_position=true"),
    mark_price: float = typer.Option(0.0, help="Optional mark/exec price override for paper fills"),
    confidence: float = typer.Option(0.0, help="Optional confidence for deterministic risk gate (0-1)"),
    position_size_pct: float = typer.Option(0.0, help="Optional position_size_pct for deterministic risk gate (0-1)"),
    allow_duplicates: bool = typer.Option(False, help="Bypass execution idempotency check for this command"),
):
    """Place and manage options/spot trades with paper-safe defaults."""
    validate_runtime_environment()

    if live:
        paper = False

    intent: Optional[TradeIntent] = None
    if intent_file:
        intent_path = Path(intent_file)
        if not intent_path.exists():
            raise typer.BadParameter(f"Intent file does not exist: {intent_path}")
        payload = json.loads(intent_path.read_text(encoding="utf-8"))
        if "order_intent" in payload and isinstance(payload["order_intent"], dict):
            payload = payload["order_intent"]
        intent = TradeIntent.from_order_intent(payload=payload, quantity=quantity)

    if show_wallet:
        console.print_json(data=get_paper_wallet_snapshot())
        return

    if show_positions:
        if paper:
            positions = get_paper_wallet_snapshot()
        else:
            positions = list_positions(broker=broker, instrument_type=instrument_type, symbol=symbol)
        console.print_json(data=positions)
        return

    if cancel_all:
        result = cancel_all_orders(
            broker=broker,
            instrument_type=instrument_type,
            symbol=symbol or None,
            paper=paper,
            product_id=product_id if product_id > 0 else None,
        )
        console.print_json(data=result)
        return

    if close_position:
        if not symbol and not intent:
            raise typer.BadParameter("symbol is required when closing a position")
        close_symbol = symbol or (intent.symbol if intent else "")
        result = close_symbol_position(
            symbol=close_symbol,
            broker=broker,
            instrument_type=instrument_type,
            paper=paper,
            security_id=security_id or None,
            product_id=product_id if product_id > 0 else None,
            net_quantity=net_quantity,
            exchange_segment=exchange_segment,
            product_type=product_type,
            mark_price=mark_price if mark_price > 0 else None,
        )
        console.print_json(data=result)
        return

    if intent is None:
        if not symbol:
            raise typer.BadParameter("symbol is required when intent_file is not provided")
        intent = TradeIntent(
            symbol=symbol,
            instrument_type=instrument_type.lower(),
            signal=signal.upper(),
            quantity=quantity,
            suggested_stop_loss=stop_loss,
            suggested_target=target,
            trailing_jump=trailing_jump,
        )

    try:
        result = execute_trade(
            intent=intent,
            broker=broker,
            paper=paper,
            security_id=security_id or None,
            product_id=product_id if product_id > 0 else None,
            exchange_segment=exchange_segment,
            product_type=product_type,
            mark_price=mark_price if mark_price > 0 else None,
            confidence=confidence if confidence > 0 else None,
            position_size_pct=position_size_pct if position_size_pct > 0 else None,
            allow_duplicates=allow_duplicates,
        )
    except RuntimeError as exc:
        console.print(f"[red]Trade execution blocked:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print_json(data=result.__dict__)


@app.command()
def execute_latest_intent(
    ticker: str = typer.Option("", help="Optional ticker filter for reports/<ticker_timestamp>/order_intent.json"),
    reports_root: str = typer.Option("reports", help="Reports root directory"),
    broker: str = typer.Option("auto", help="auto, dhan, delta"),
    quantity: float = typer.Option(1.0, min=0.0001, help="Order quantity override"),
    paper: bool = typer.Option(True, help="Paper mode (safe default)"),
    live: bool = typer.Option(False, help="Set --live to place live order"),
    security_id: str = typer.Option("", help="Optional Dhan security ID override"),
    product_id: int = typer.Option(0, help="Optional Delta product_id override"),
    exchange_segment: str = typer.Option("NSE_FNO", help="Dhan exchange segment"),
    product_type: str = typer.Option("INTRADAY", help="Dhan product type"),
    mark_price: float = typer.Option(0.0, help="Optional mark/exec price override for paper fills"),
    allow_duplicates: bool = typer.Option(False, help="Bypass execution idempotency check for this command"),
):
    """Execute the latest saved order_intent.json from reports in paper/live mode."""
    validate_runtime_environment()

    if live:
        paper = False

    latest = find_latest_order_intent_file(Path(reports_root), ticker=ticker)
    if latest is None:
        raise typer.BadParameter("No order_intent.json found in reports directory")

    payload = json.loads(latest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise typer.BadParameter(f"Invalid JSON payload in {latest}")

    intent = TradeIntent.from_order_intent(payload=payload, quantity=quantity)
    metadata = {
        "confidence": payload.get("confidence"),
        "position_size_pct": payload.get("position_size_pct"),
    }
    try:
        result = execute_trade(
            intent=intent,
            broker=broker,
            paper=paper,
            security_id=security_id or None,
            product_id=product_id if product_id > 0 else None,
            exchange_segment=exchange_segment,
            product_type=product_type,
            mark_price=mark_price if mark_price > 0 else None,
            allow_duplicates=allow_duplicates,
            **metadata,
        )
    except RuntimeError as exc:
        console.print(f"[red]Intent execution blocked:[/red] {exc}")
        raise typer.Exit(code=1)
    out = result.__dict__.copy()
    out["intent_file"] = str(latest)
    console.print_json(data=out)


@app.command()
def run_cycles(
    cycles: int = typer.Option(5, min=1, help="Number of execution cycles"),
    every_seconds: float = typer.Option(30.0, min=0.5, help="Delay between cycles"),
    ticker: str = typer.Option("", help="Optional ticker filter for latest intent lookup"),
    reports_root: str = typer.Option("reports", help="Reports root directory"),
    broker: str = typer.Option("auto", help="auto, dhan, delta"),
    quantity: float = typer.Option(1.0, min=0.0001, help="Order quantity override"),
    paper: bool = typer.Option(True, help="Paper mode by default"),
    live: bool = typer.Option(False, help="Set --live to place real orders"),
    mark_price: float = typer.Option(0.0, help="Optional paper mark price override"),
    allow_repeats: bool = typer.Option(False, help="Allow repeated execution of the same unchanged intent file"),
    resume: bool = typer.Option(True, help="Resume from saved cycle state if available"),
    force_restart: bool = typer.Option(False, help="Ignore saved state and restart from cycle 1"),
    state_file: str = typer.Option("", help="Optional path to cycle state file"),
):
    """Run a lightweight execution loop using latest saved order intents."""
    validate_runtime_environment()

    if live:
        paper = False

    state_path = Path(state_file) if state_file else (Path(reports_root) / ".runtime" / "cycle_state.json")
    state_store = CycleStateStore(state_path)

    if force_restart:
        resume = False
        state_store.reset()

    context_key = f"{Path(reports_root).resolve()}::{ticker.upper()}::{broker}::{paper}::{quantity}"
    start_cycle = 1
    if resume:
        start_cycle = state_store.resume_start_cycle(context_key=context_key, total_cycles=cycles)
        if start_cycle > cycles:
            console.print("[yellow]No cycles remaining; saved state already completed requested range.[/yellow]")
            return

    shutdown_manager = GracefulShutdownManager()
    shutdown_manager.setup_signal_handlers()

    table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
    table.add_column("Cycle", style="cyan")
    table.add_column("Symbol", style="green")
    table.add_column("Signal", style="yellow")
    table.add_column("Status", style="white")
    table.add_column("Mode", style="white")

    last_fingerprint = ""

    last_completed = start_cycle - 1

    try:
        for idx in range(start_cycle, cycles + 1):
            if shutdown_manager.shutdown_requested:
                state_store.mark_interrupted(
                    context_key=context_key,
                    last_completed_cycle=last_completed,
                    total_cycles=cycles,
                    reason=shutdown_manager.reason or "shutdown_requested",
                )
                table.add_row(str(idx), "-", "-", "interrupted", "paper" if paper else "live")
                break

            try:
                latest = find_latest_order_intent_file(Path(reports_root), ticker=ticker)
                if latest is None:
                    raise RuntimeError("No order_intent.json found in reports directory")

                payload_text = latest.read_text(encoding="utf-8")
                payload = json.loads(payload_text)
                intent = TradeIntent.from_order_intent(payload=payload, quantity=quantity)
                fingerprint = f"{latest.resolve()}::{latest.stat().st_mtime_ns}::{quantity}::{broker}::{paper}"

                if (not allow_repeats) and fingerprint == last_fingerprint:
                    status = "skipped_duplicate"
                    table.add_row(str(idx), intent.symbol, intent.signal, status, "paper" if paper else "live")
                    last_completed = idx
                    state_store.mark_cycle_success(
                        context_key=context_key,
                        cycle_idx=idx,
                        status=status,
                        last_fingerprint=fingerprint,
                        total_cycles=cycles,
                    )
                    if idx < cycles:
                        time.sleep(float(every_seconds))
                    continue

                metadata = {
                    "confidence": payload.get("confidence"),
                    "position_size_pct": payload.get("position_size_pct"),
                }

                try:
                    result = execute_trade(
                        intent=intent,
                        broker=broker,
                        paper=paper,
                        mark_price=mark_price if mark_price > 0 else None,
                        allow_duplicates=allow_repeats,
                        **metadata,
                    )
                    status = result.status
                except RuntimeError as exc:
                    status = f"blocked: {exc}"

                table.add_row(str(idx), intent.symbol, intent.signal, status, "paper" if paper else "live")
                last_fingerprint = fingerprint
                last_completed = idx
                state_store.mark_cycle_success(
                    context_key=context_key,
                    cycle_idx=idx,
                    status=status,
                    last_fingerprint=fingerprint,
                    total_cycles=cycles,
                )

                if idx < cycles:
                    time.sleep(float(every_seconds))

            except Exception as exc:
                state_store.mark_cycle_failure(
                    context_key=context_key,
                    cycle_idx=idx,
                    error=str(exc),
                    total_cycles=cycles,
                )
                table.add_row(str(idx), "SYSTEM", "ERROR", f"failed: {exc}", "paper" if paper else "live")
                if idx < cycles:
                    time.sleep(float(every_seconds))

        console.print("\n[bold cyan]Cycle Runner Complete[/bold cyan]")
        console.print(table)
        if paper:
            console.print_json(data=get_paper_wallet_snapshot())
    finally:
        shutdown_manager.restore_signal_handlers()


@app.command()
def show_journal(
    limit: int = typer.Option(30, min=1, help="Number of latest journal records"),
):
    """Show latest append-only decision journal entries."""
    rows = read_journal_tail(limit=limit)
    console.print_json(data={"entries": rows, "count": len(rows)})


@app.command()
def daily_summary(
    day_utc: str = typer.Option("", help="UTC date in YYYY-MM-DD (default: today UTC)"),
    limit_scan_lines: int = typer.Option(100000, min=1000, help="Max journal lines to scan"),
):
    """Show end-of-day style summary from execution journal."""
    day = day_utc.strip() or None
    try:
        summary = get_daily_summary(day_utc=day, limit_scan_lines=limit_scan_lines)
    except ValueError:
        raise typer.BadParameter("day_utc must be in YYYY-MM-DD format")
    console.print_json(data=summary)


@app.command()
def governance_report(
    symbol: str = typer.Option("BTC-USD", help="Symbol used in autolab artifacts"),
    runs_limit: int = typer.Option(10, min=1, help="How many latest autolab runs to include"),
):
    """Show strategy promotion governance snapshot and recent autolab runs."""
    report = build_governance_report(symbol=symbol, runs_limit=runs_limit)
    if not report.get("artifacts_found", False):
        console.print(
            f"[yellow]No strategy governance artifacts found for {symbol} under {report['base_dir']}[/yellow]"
        )
    console.print_json(data=report)


@app.command()
def ops_report(
    symbol: str = typer.Option("BTC-USD", help="Symbol used in strategy governance artifacts"),
    journal_limit: int = typer.Option(10, min=1, help="How many latest journal entries to include"),
    day_utc: str = typer.Option("", help="UTC date in YYYY-MM-DD for daily summary (default: today UTC)"),
    limit_scan_lines: int = typer.Option(100000, min=1000, help="Max journal lines to scan for daily summary"),
    runs_limit: int = typer.Option(10, min=1, help="How many latest autolab runs to include in governance section"),
    output: str = typer.Option("json", help="Output mode: json or table"),
):
    """Show a consolidated operations report (runtime, daily summary, governance)."""
    day = day_utc.strip() or None
    output_mode = output.strip().lower()
    if output_mode not in {"json", "table"}:
        raise typer.BadParameter("output must be one of: json, table")

    try:
        report = build_ops_report(
            symbol=symbol,
            journal_limit=journal_limit,
            day_utc=day,
            limit_scan_lines=limit_scan_lines,
            governance_runs_limit=runs_limit,
        )
    except ValueError:
        raise typer.BadParameter("day_utc must be in YYYY-MM-DD format")

    governance = report.get("governance_report", {})
    if isinstance(governance, dict) and (not governance.get("artifacts_found", False)):
        console.print(
            f"[yellow]No strategy governance artifacts found for {symbol} under {governance.get('base_dir', 'N/A')}[/yellow]"
        )
    if output_mode == "json":
        console.print_json(data=report)
    else:
        render_ops_report_table(report)


@app.command()
def runtime_status(
    journal_limit: int = typer.Option(10, min=1, help="How many latest journal entries to include"),
):
    """Show compact runtime status: wallet, today's execution count, and recent journal rows."""
    wallet = get_paper_wallet_snapshot()
    today_exec = count_today_executions(statuses={"simulated_filled", "submitted"})
    rows = read_journal_tail(limit=journal_limit)
    console.print_json(
        data={
            "today_executions": today_exec,
            "paper_wallet": wallet,
            "recent_journal": rows,
        }
    )


@app.command()
def reset_wallet(
    yes: bool = typer.Option(False, help="Required flag to confirm wallet reset"),
):
    """Reset local paper wallet state (positions + orders + cash to initial balance)."""
    if not yes:
        console.print("[red]Wallet reset blocked:[/red] pass --yes to confirm")
        raise typer.Exit(code=1)
    wallet = PositionManager.from_env()
    wallet.reset()
    console.print_json(data={"status": "reset", "wallet": wallet.get_summary()})


if __name__ == "__main__":
    app()
