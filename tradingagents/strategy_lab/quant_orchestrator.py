"""
Module: quant_orchestrator.py
Part of the strategy_lab subsystem.

This module contains logic for the strategy_lab operations as part of the broader TradingAgents framework.
"""
import subprocess
import json
import logging
import os
import shutil
from pathlib import Path
from tradingagents.agents.trader.quant import create_quant_agent
from tradingagents.ops.notifier import send_notification

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_quant_cycle(llm, symbol: str, market_context: str, max_retries=3):
    """
    1. Ask LLM to generate vectorbt script based on context
    2. Save it
    3. Run it in a subprocess
    4. Feedback loop: Check the stdout result and check for errors. Feed to LLM if failed.
    5. Save to Active_Strategies if Sharpe > 1.0
    """
    quant_node = create_quant_agent(llm)
    state = {
        "company_of_interest": symbol,
        "market_report": market_context,
        "quant_feedback": None
    }
    
    script_dir = Path("strategy_lab_results") / "scripts"
    approved_dir = Path("strategy_lab_results") / "approved_scripts"
    os.makedirs(script_dir, exist_ok=True)
    os.makedirs(approved_dir, exist_ok=True)
    
    out_path = script_dir / f"strategy_{symbol.replace('-', '_')}_vbt.py"
    
    for attempt in range(max_retries):
        logger.info(f"Quant generation attempt {attempt + 1}/{max_retries} for {symbol}")
        result = quant_node(state)
        
        if not out_path.exists():
            logger.error("Failed to generate code on disk.")
            continue
            
        logger.info(f"Executing sandboxed vectorbt script: {out_path}")
        try:
            # Run the script inside an isolated, network-disabled Docker container
            script_abs_path = out_path.absolute()
            sandbox_image = os.getenv("QUANT_SANDBOX_IMAGE", "python:3.10-slim")
            
            # Using bare minimum python image by default, but you should build a custom image
            # 'tradingagents-quant-sandbox' with vectorbt/pandas pre-installed in production.
            docker_cmd = [
                "docker", "run", "--rm",
                "--network", "none", # Total network isolation
                "--memory", "512m",  # Limit memory
                "--cpus", "1.0",     # Limit compute
                "-v", f"{script_abs_path}:/app/script.py:ro",
                sandbox_image,
                "python", "/app/script.py"
            ]

            # Fallback for local testing if docker isn't running (Not recommended for prod)
            if os.getenv("DISABLE_DOCKER_SANDBOX", "false").lower() == "true":
                logger.warning("Docker sandbox disabled! Running natively (UNSAFE).")
                process = subprocess.run(
                    ["python", str(out_path)],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
            else:
                process = subprocess.run(
                    docker_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120
                )

            output_log = {
                "symbol": symbol,
                "stdout": process.stdout,
                "stderr": process.stderr,
                "exit_code": process.returncode
            }
            
            log_path = script_dir / f"{symbol.replace('-', '_')}_vbt_output.json"
            with open(log_path, "w") as f:
                json.dump(output_log, f, indent=2)
                
            if process.returncode != 0:
                logger.warning(f"Script failed with exit code: {process.returncode}")
                # Pass feedback for next iteration
                state["quant_feedback"] = process.stderr
                continue
            
            # If successful, parse the last line of JSON
            lines = process.stdout.strip().split("\n")
            json_metrics = None
            for line in reversed(lines):
                try:
                    if line.startswith("{") and line.endswith("}"):
                        json_metrics = json.loads(line)
                        break
                except Exception:
                    pass
            
            if json_metrics:
                sharpe = json_metrics.get("sharpe", 0.0)
                logger.info(f"Metrics parsed successfully: Sharpe={sharpe}")
                if sharpe >= 1.0:
                    approved_path = approved_dir / f"strategy_{symbol.replace('-', '_')}_approved.py"
                    shutil.copy(out_path, approved_path)
                    logger.info(f"Strategy promoted! Saved to {approved_path}")
                    send_notification(f"🧪 **New Strategy Promoted!** 🧪\nSymbol: {symbol}\nSharpe Ratio: {sharpe}\nSaved to: {approved_path.name}")
                    return True
                else:
                    state["quant_feedback"] = f"Code executed fine, but Sharpe ratio ({sharpe}) was less than 1.0. Aim for a better edge."
            else:
                state["quant_feedback"] = "Output lacked the final JSON metrics dictionary in stdout. Ensure you print the JSON exactly as instruction."
                
        except subprocess.TimeoutExpired:
            logger.error("Script execution timed out.")
            state["quant_feedback"] = "Script timed out (took > 120s). Optimize algorithms."
            
    logger.error("Failed to generate a profitable strategy after max retries.")
    return False
