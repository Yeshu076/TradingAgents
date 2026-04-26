<p align="center">
  <img src="assets/TauricResearch.png" style="width: 60%; height: auto;">
</p>

<div align="center" style="line-height: 1;">
  <a href="https://arxiv.org/abs/2412.20138" target="_blank"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2412.20138-B31B1B?logo=arxiv"/></a>
  <a href="https://discord.com/invite/hk9PGKShPK" target="_blank"><img alt="Discord" src="https://img.shields.io/badge/Discord-TradingResearch-7289da?logo=discord&logoColor=white&color=7289da"/></a>
  <a href="./assets/wechat.png" target="_blank"><img alt="WeChat" src="https://img.shields.io/badge/WeChat-TauricResearch-brightgreen?logo=wechat&logoColor=white"/></a>
  <a href="https://x.com/TauricResearch" target="_blank"><img alt="X Follow" src="https://img.shields.io/badge/X-TauricResearch-white?logo=x&logoColor=white"/></a>
  <br>
  <a href="https://github.com/TauricResearch/" target="_blank"><img alt="Community" src="https://img.shields.io/badge/Join_GitHub_Community-TauricResearch-14C290?logo=discourse"/></a>
</div>

<div align="center">
  <!-- Keep these links. Translations will automatically update with the README. -->
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=de">Deutsch</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=es">Español</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=fr">français</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ja">日本語</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ko">한국어</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=pt">Português</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ru">Русский</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=zh">中文</a>
</div>

---

# TradingAgents: Multi-Agents LLM Financial Trading Framework

## News
- [2026-03] **TradingAgents v0.2.2** released with GPT-5.4/Gemini 3.1/Claude 4.6 model coverage, five-tier rating scale, OpenAI Responses API, Anthropic effort control, and cross-platform stability.
- [2026-02] **TradingAgents v0.2.0** released with multi-provider LLM support (GPT-5.x, Gemini 3.x, Claude 4.x, Grok 4.x) and improved system architecture.
- [2026-01] **Trading-R1** [Technical Report](https://arxiv.org/abs/2509.11420) released, with [Terminal](https://github.com/TauricResearch/Trading-R1) expected to land soon.

<div align="center">
<a href="https://www.star-history.com/#TauricResearch/TradingAgents&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date" />
   <img alt="TradingAgents Star History" src="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date" style="width: 80%; height: auto;" />
 </picture>
</a>
</div>

> 🎉 **TradingAgents** officially released! We have received numerous inquiries about the work, and we would like to express our thanks for the enthusiasm in our community.
>
> So we decided to fully open-source the framework. Looking forward to building impactful projects with you!

<div align="center">

🚀 [TradingAgents](#tradingagents-framework) | ⚡ [Installation & CLI](#installation-and-cli) | 🎬 [Demo](https://www.youtube.com/watch?v=90gr5lwjIho) | 📦 [Package Usage](#tradingagents-package) | 🤝 [Contributing](#contributing) | 📄 [Citation](#citation)

</div>

## TradingAgents Framework

TradingAgents is a multi-agent trading framework that mirrors the dynamics of real-world trading firms. By deploying specialized LLM-powered agents: from fundamental analysts, sentiment experts, and technical analysts, to trader, risk management team, the platform collaboratively evaluates market conditions and informs trading decisions. Moreover, these agents engage in dynamic discussions to pinpoint the optimal strategy.

<p align="center">
  <img src="assets/schema.png" style="width: 100%; height: auto;">
</p>

> TradingAgents framework is designed for research purposes. Trading performance may vary based on many factors, including the chosen backbone language models, model temperature, trading periods, the quality of data, and other non-deterministic factors. [It is not intended as financial, investment, or trading advice.](https://tauric.ai/disclaimer/)

Our framework decomposes complex trading tasks into specialized roles. This ensures the system achieves a robust, scalable approach to market analysis and decision-making.

### Analyst Team
- Fundamentals Analyst: Evaluates company financials and performance metrics, identifying intrinsic values and potential red flags.
- Sentiment Analyst: Analyzes social media and public sentiment using sentiment scoring algorithms to gauge short-term market mood.
- News Analyst: Monitors global news and macroeconomic indicators, interpreting the impact of events on market conditions.
- Technical Analyst: Utilizes technical indicators (like MACD and RSI) to detect trading patterns and forecast price movements.

<p align="center">
  <img src="assets/analyst.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

### Researcher Team
- Comprises both bullish and bearish researchers who critically assess the insights provided by the Analyst Team. Through structured debates, they balance potential gains against inherent risks.

<p align="center">
  <img src="assets/researcher.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Trader Agent
- Composes reports from the analysts and researchers to make informed trading decisions. It determines the timing and magnitude of trades based on comprehensive market insights.

<p align="center">
  <img src="assets/trader.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Risk Management and Portfolio Manager
- Continuously evaluates portfolio risk by assessing market volatility, liquidity, and other risk factors. The risk management team evaluates and adjusts trading strategies, providing assessment reports to the Portfolio Manager for final decision.
- The Portfolio Manager approves/rejects the transaction proposal. If approved, the order will be sent to the simulated exchange and executed.

<p align="center">
  <img src="assets/risk.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

## Installation and CLI

### Installation

Clone TradingAgents:
```bash
git clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents
```

Create a virtual environment in any of your favorite environment managers:
```bash
conda create -n tradingagents python=3.13
conda activate tradingagents
```

Install the package and its dependencies:
```bash
pip install .
```

### Required APIs

TradingAgents supports multiple LLM providers. Set the API key for your chosen provider:

```bash
export OPENAI_API_KEY=...          # OpenAI (GPT)
export GOOGLE_API_KEY=...          # Google (Gemini)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
export XAI_API_KEY=...             # xAI (Grok)
export OPENROUTER_API_KEY=...      # OpenRouter
export ALPHA_VANTAGE_API_KEY=...   # Alpha Vantage
```

For local models, configure Ollama with `llm_provider: "ollama"` in your config.

For production multi-asset routing used in this workspace:

```bash
# Delta Exchange (crypto derivatives snapshot provider)
export DELTA_REST_BASE_URL=https://api.india.delta.exchange
export DELTA_API_KEY=...
export DELTA_API_SECRET=...

# Dhan (Nifty options chain provider)
export DHAN_CLIENT_ID=...
export DHAN_ACCESS_TOKEN=...

# Optional Nifty underlying overrides
export DHAN_NIFTY_SECURITY_ID=13
export DHAN_NIFTY_UNDERLYING_SEGMENT=IDX_I

# Execution guardrails (safe defaults)
export TRADINGAGENTS_ALLOW_LIVE=false
export TRADINGAGENTS_MAX_ORDER_QTY=25
export TRADINGAGENTS_MAX_ORDER_NOTIONAL=500000
export TRADINGAGENTS_MAX_DAILY_TRADES=50
export TRADINGAGENTS_ALLOWED_INSTRUMENTS=options,spot,crypto,forex,equity
export TRADINGAGENTS_ENFORCE_MARKET_HOURS=true

# Deterministic pre-execution risk gate
export TRADINGAGENTS_RISK_MIN_CONFIDENCE=0.40
export TRADINGAGENTS_RISK_MIN_RR=1.20
export TRADINGAGENTS_RISK_MAX_POSITION_PCT=0.15

# Persistent local paper wallet
export TRADINGAGENTS_PAPER_INITIAL_BALANCE=1000000
export TRADINGAGENTS_PAPER_STATE_FILE=paper_wallet.json

# Execution idempotency (duplicate protection)
export TRADINGAGENTS_EXECUTION_IDEMPOTENCY_ENABLED=true
export TRADINGAGENTS_EXECUTION_IDEMPOTENCY_WINDOW_SECONDS=3600
export TRADINGAGENTS_EXECUTION_IDEMPOTENCY_SCAN_LIMIT=1000

# Broker/API resilience
export TRADINGAGENTS_RETRY_MAX_ATTEMPTS=3
export TRADINGAGENTS_RETRY_BASE_DELAY_SECONDS=0.25
export TRADINGAGENTS_RETRY_MAX_DELAY_SECONDS=2.0
export TRADINGAGENTS_RETRY_JITTER_RATIO=0.15
export TRADINGAGENTS_CIRCUIT_FAILURE_THRESHOLD=3
export TRADINGAGENTS_CIRCUIT_RESET_SECONDS=60
export TRADINGAGENTS_HTTP_TIMEOUT_SECONDS=15

# Append-only decision journal
export TRADINGAGENTS_DECISION_JOURNAL_FILE=trade_decisions.jsonl
export TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY=true
export TRADINGAGENTS_DECISION_JOURNAL_MAX_BYTES=5000000
export TRADINGAGENTS_DECISION_JOURNAL_MAX_ROLL_FILES=5
```

Default routing now prefers:
- Crypto derivatives: `delta,binance,bybit`
- Options chain: `dhan,yfinance`

You can override these in `DEFAULT_CONFIG["data_vendors"]` or runtime config.

Alternatively, copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```

### CLI Usage

Launch the interactive CLI:
```bash
tradingagents          # installed command
python -m cli.main     # alternative: run directly from source
```
You will see a screen where you can select your desired tickers, analysis date, LLM provider, research depth, and more.

Non-interactive production commands:

```bash
# Place/manage options and spot trades (paper mode by default)
tradingagents trade --symbol NIFTY25SEP24500CE --instrument-type options --signal BUY --quantity 1

# Intentionally place repeated order (bypass idempotency for this invocation)
tradingagents trade --symbol NIFTY25SEP24500CE --instrument-type options --signal BUY --quantity 1 --allow-duplicates

# Live mode requires TRADINGAGENTS_ALLOW_LIVE=true and broker credentials
tradingagents trade --symbol NIFTY25SEP24500CE --instrument-type options --signal BUY --quantity 1 --live

# Execute latest saved reports/*/order_intent.json in one step
tradingagents execute-latest-intent --ticker NIFTY --reports-root reports

# Run repeated execution cycles from latest order_intent (paper/live)
tradingagents run-cycles --cycles 5 --every-seconds 30 --ticker NIFTY --reports-root reports

# Resume an interrupted cycle run from saved state (default behavior)
tradingagents run-cycles --cycles 5 --ticker NIFTY --reports-root reports --resume

# Force a clean restart and ignore prior cycle state
tradingagents run-cycles --cycles 5 --ticker NIFTY --reports-root reports --force-restart

# Inspect persistent paper wallet snapshot
tradingagents trade --show-wallet

# Inspect latest decision journal entries
tradingagents show-journal --limit 30

# End-of-day summary from journal (today UTC or explicit date)
tradingagents daily-summary
tradingagents daily-summary --day-utc 2026-03-28

# Runtime operations dashboard (wallet + today's execution count + recent journal)
tradingagents runtime-status --journal-limit 10

# Reset paper wallet explicitly (requires confirmation flag)
tradingagents reset-wallet --yes
```

Notes:
- For Dhan options live orders, `security_id` is auto-resolved when possible from option chain.
- If auto-resolution cannot infer the contract, pass `--security-id` explicitly.

<p align="center">
  <img src="assets/cli/cli_init.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

An interface will appear showing results as they load, letting you track the agent's progress as it runs.

<p align="center">
  <img src="assets/cli/cli_news.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

<p align="center">
  <img src="assets/cli/cli_transaction.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

## TradingAgents Package

### Implementation Details

We built TradingAgents with LangGraph to ensure flexibility and modularity. The framework supports multiple LLM providers: OpenAI, Google, Anthropic, xAI, OpenRouter, and Ollama.

### Python Usage

To use TradingAgents inside your code, you can import the `tradingagents` module and initialize a `TradingAgentsGraph()` object. The `.propagate()` function will return a decision. You can run `main.py`, here's also a quick example:

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG.copy())

# forward propagate
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

You can also adjust the default configuration to set your own choice of LLMs, debate rounds, etc.

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"        # openai, google, anthropic, xai, openrouter, ollama
config["deep_think_llm"] = "gpt-5.2"     # Model for complex reasoning
config["quick_think_llm"] = "gpt-5-mini" # Model for quick tasks
config["max_debate_rounds"] = 2

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

See `tradingagents/default_config.py` for all configuration options.

## Contributing

We welcome contributions from the community! Whether it's fixing a bug, improving documentation, or suggesting a new feature, your input helps make this project better. If you are interested in this line of research, please consider joining our open-source financial AI research community [Tauric Research](https://tauric.ai/).

## Citation

Please reference our work if you find *TradingAgents* provides you with some help :)

```
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework}, 
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138}, 
}
```
