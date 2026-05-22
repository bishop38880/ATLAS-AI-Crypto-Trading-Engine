-- 002_post_trade_learning.sql — Add exit_reason to signal_history

-- Add the new column from the Post-Trade Learning MCP
ALTER TABLE signal_history
ADD COLUMN IF NOT EXISTS exit_reason TEXT;

-- Create an index to quickly filter outcomes for the lesson generator / UI
CREATE INDEX IF NOT EXISTS idx_signal_history_outcome 
ON signal_history (outcome_label, exit_reason);
