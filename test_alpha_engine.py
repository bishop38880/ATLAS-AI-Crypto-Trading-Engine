import asyncio
from openai import AsyncOpenAI
import json

# Point this to your local LM Studio server
client = AsyncOpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")

SYSTEM_PROMPT = """You are the Neuro-Symbolic Alpha Engine for ATLAS, an institutional-grade cryptocurrency trading system. 

[CONTEXT]
You are receiving a finalized Intelligence Matrix for a specific asset. The deterministic math has already been calculated. The `overall_score` represents the hard numerical confluence across all agents. You must absolutely NOT recalculate, modify, or question the `overall_score`. 

[ACTION]
Your sole objective is Cross-Category Correlation. You must read the `sub_signals` flags across different agents and synthesize them to find semantic convergences (the "Green Light") or fatal contradictions (the "Red Light"). 
- Look for traps: e.g., TechnicalAgent shows bullish momentum, but WhaleWatcherAgent shows distribution and DerivativesAgent shows dropping Open Interest (fakeout).
- Look for squeezes: e.g., LiquidationAgent shows dense clusters combined with extreme short crowding and rising Open Interest.

[RESULT]
You must evaluate the matrix and output a decision using strictly the following JSON schema. You do NOT dictate position sizing or risk percentage (that is handled downstream).
{
  "decision": "Strong Buy" | "Buy" | "Hold" | "Sell" | "Strong Sell" | "No Position",
  "correlation_grade": "STANDARD" | "ELEVATED" | "EXTREME",
  "confidence": float (0.0 to 1.0),
  "key_convergences": [list of strings explaining aligned flags],
  "key_risks": [list of strings explaining contradictions or trap warnings],
  "reasoning_summary": "Concise 3-sentence explanation of the correlation logic."
}

[LENGTH]
Maximum 500 tokens. No preamble. No markdown formatting. JSON only."""

# Paste the "Bull Trap" JSON example here as a string
MOCK_MATRIX = """
{
  "signal_id": "a1b2c3d4-5678-90ef-ghij-klmnopqrstuv",
  "overall_score": 145,
  "matrix": {
    "TechnicalAgent": { "score": 45, "sub_signals": { "momentum": { "value": "Golden Cross", "flag": "BULLISH_MOMENTUM" } } },
    "DerivativesAgent": { "score": 15, "sub_signals": { "oi_divergence": { "value": "-8.2%", "flag": "SEVERE_OI_DROP" } } },
    "WhaleWatcherAgent": { "score": 10, "sub_signals": { "exchange_netflow": { "value": "+12000 ETH", "flag": "EXCHANGE_INFLOW_WARNING" }, "smart_money_flow": { "value": "Distributing", "flag": "RETAIL_BAG_HOLDING" } } }
  }
}
"""

async def run_test():
    print("🚀 Firing Intelligence Matrix to DeepSeek Alpha Engine...")
    
    response = await client.chat.completions.create(
        model="local-model", # LM Studio ignores this, it uses whatever is loaded
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Analyze the following Intelligence Matrix:\n\n{MOCK_MATRIX}"}
        ],
        temperature=0.1, # Keep it cold and logical
        max_tokens=500
    )
    
    raw_output = response.choices[0].message.content
    print("\n[RAW LLM OUTPUT]")
    print(raw_output)
    
    print("\n🛠️ Testing JSON Parser (Crash Test)...")
    try:
        # If it hallucinates markdown like ```json, this will catch it
        clean_json = raw_output.replace("```json", "").replace("```", "").strip()
        parsed_decision = json.loads(clean_json)
        print("✅ SUCCESS! The payload parsed perfectly into a Python dictionary.")
        print(f"Decision: {parsed_decision.get('decision')}")
    except Exception as e:
        print(f"❌ FAIL: The LLM output broke the JSON parser. Error: {e}")

if __name__ == "__main__":
    asyncio.run(run_test())