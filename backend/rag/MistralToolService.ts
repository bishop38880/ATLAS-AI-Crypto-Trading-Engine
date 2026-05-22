/**
 * ATLAS RAG Pipeline - Mistral Small 3 Tool Calling Service
 *
 * Orchestrates data gathering across multiple sources using
 * Mistral Small 3's native tool calling capabilities.
 *
 * Cost: ~$0.10-0.30 per million tokens
 * Latency: ~200-500ms for tool selection
 */

import Mistral from '@mistralai/mistralai';
import { EventEmitter } from 'events';

// ============================================================
// Types
// ============================================================

interface Tool {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters: {
      type: 'object';
      properties: Record<string, any>;
      required?: string[];
    };
  };
}

interface ToolCall {
  id: string;
  type: 'function';
  function: {
    name: string;
    arguments: string;
  };
}

interface ToolResult {
  tool_call_id: string;
  role: 'tool';
  content: string;
}

interface MistralConfig {
  apiKey: string;
  model: string;
  maxTokens: number;
  temperature: number;
  maxToolRounds: number;
}

interface DataSourceClients {
  coinglass: any;
  lunarcrush: any;
  nansen: any;
  technical: any;
  rag: any;
}

// ============================================================
// Tool Definitions
// ============================================================

const ATLAS_TOOLS: Tool[] = [
  // Derivatives Data (CoinGlass)
  {
    type: 'function',
    function: {
      name: 'get_derivatives_data',
      description: 'Fetch derivatives metrics from CoinGlass: funding rates, open interest, liquidations, long/short ratios. Essential for gauging leveraged positioning.',
      parameters: {
        type: 'object',
        properties: {
          symbol: {
            type: 'string',
            description: 'Crypto symbol',
            enum: ['BTC', 'ETH', 'SOL', 'XRP', 'XLM', 'ICP', 'LDO', 'JUP']
          },
          metrics: {
            type: 'array',
            items: {
              type: 'string',
              enum: ['funding_rate', 'open_interest', 'liquidations', 'long_short_ratio', 'oi_weighted_funding']
            },
            description: 'Specific metrics to fetch. Omit for all.'
          }
        },
        required: ['symbol']
      }
    }
  },

  // Social Sentiment (LunarCrush)
  {
    type: 'function',
    function: {
      name: 'get_social_sentiment',
      description: 'Fetch social sentiment from LunarCrush: galaxy score, alt rank, social volume, sentiment score, social dominance.',
      parameters: {
        type: 'object',
        properties: {
          symbol: {
            type: 'string',
            description: 'Crypto symbol'
          },
          timeframe: {
            type: 'string',
            enum: ['1h', '4h', '24h', '7d'],
            description: 'Lookback period'
          },
          include_influencers: {
            type: 'boolean',
            description: 'Include top influencer activity'
          }
        },
        required: ['symbol']
      }
    }
  },

  // Whale Activity (Nansen)
  {
    type: 'function',
    function: {
      name: 'get_whale_activity',
      description: 'Fetch whale wallet movements from Nansen: smart money flows, exchange inflows/outflows, accumulation patterns.',
      parameters: {
        type: 'object',
        properties: {
          symbol: {
            type: 'string',
            description: 'Crypto symbol'
          },
          activity_type: {
            type: 'string',
            enum: ['accumulation', 'distribution', 'exchange_inflow', 'exchange_outflow', 'smart_money'],
            description: 'Type of whale activity'
          },
          timeframe: {
            type: 'string',
            enum: ['1h', '4h', '24h', '7d'],
            description: 'Lookback period'
          }
        },
        required: ['symbol']
      }
    }
  },

  // Technical Indicators
  {
    type: 'function',
    function: {
      name: 'get_technical_indicators',
      description: 'Calculate technical indicators: RSI, MACD, Bollinger Bands, EMAs, ATR, volume profile.',
      parameters: {
        type: 'object',
        properties: {
          symbol: {
            type: 'string',
            description: 'Crypto symbol'
          },
          timeframe: {
            type: 'string',
            enum: ['15m', '1h', '4h', '1d'],
            description: 'Candle timeframe'
          },
          indicators: {
            type: 'array',
            items: {
              type: 'string',
              enum: ['rsi', 'macd', 'bollinger', 'ema_ribbon', 'atr', 'volume_profile', 'vwap', 'market_structure']
            },
            description: 'Indicators to calculate'
          }
        },
        required: ['symbol', 'timeframe']
      }
    }
  },

  // Market Context
  {
    type: 'function',
    function: {
      name: 'get_market_context',
      description: 'Fetch broader market context: Fear & Greed Index, BTC dominance, total market cap, market regime classification.',
      parameters: {
        type: 'object',
        properties: {
          include_historical: {
            type: 'boolean',
            description: 'Include 7-day historical comparison'
          },
          include_correlations: {
            type: 'boolean',
            description: 'Include BTC correlation data'
          }
        },
        required: []
      }
    }
  },

  // RAG Knowledge Search
  {
    type: 'function',
    function: {
      name: 'search_knowledge_base',
      description: 'Search ATLAS knowledge base for relevant trading patterns, historical setups, or strategy documentation.',
      parameters: {
        type: 'object',
        properties: {
          query: {
            type: 'string',
            description: 'Natural language search query'
          },
          category: {
            type: 'string',
            enum: ['patterns', 'setups', 'strategies', 'risk_rules', 'market_regimes'],
            description: 'Knowledge category filter'
          },
          top_k: {
            type: 'number',
            description: 'Number of results (default 5)'
          }
        },
        required: ['query']
      }
    }
  },

  // Similar Historical Setups
  {
    type: 'function',
    function: {
      name: 'find_similar_setups',
      description: 'Find historically similar market setups from vector database. Returns past outcomes for pattern matching.',
      parameters: {
        type: 'object',
        properties: {
          conditions: {
            type: 'object',
            description: 'Current market conditions object to match',
            properties: {
              funding_rate: { type: 'number' },
              rsi: { type: 'number' },
              sentiment_score: { type: 'number' },
              whale_activity: { type: 'string' }
            }
          },
          min_similarity: {
            type: 'number',
            description: 'Minimum cosine similarity (0-1)'
          }
        },
        required: ['conditions']
      }
    }
  },

  // Position Sizing
  {
    type: 'function',
    function: {
      name: 'calculate_position',
      description: 'Calculate position size based on confluence score and risk parameters.',
      parameters: {
        type: 'object',
        properties: {
          confluence_score: {
            type: 'number',
            description: 'Total confluence score (0-220)'
          },
          direction: {
            type: 'string',
            enum: ['long', 'short'],
            description: 'Trade direction'
          },
          entry_price: {
            type: 'number',
            description: 'Planned entry price'
          },
          stop_loss_price: {
            type: 'number',
            description: 'Stop loss price'
          }
        },
        required: ['confluence_score', 'direction']
      }
    }
  }
];

// ============================================================
// Mistral Tool Calling Service
// ============================================================

export class MistralToolService extends EventEmitter {
  private client: Mistral;
  private config: MistralConfig;
  private dataSources: DataSourceClients;
  private toolCallCache: Map<string, { result: any; timestamp: number }> = new Map();
  private cacheTTL = 30000; // 30 second cache for market data

  constructor(dataSources: DataSourceClients, config?: Partial<MistralConfig>) {
    super();

    this.config = {
      apiKey: process.env.MISTRAL_API_KEY!,
      model: 'mistral-small-2503',
      maxTokens: 1024,
      temperature: 0.1,
      maxToolRounds: 3,
      ...config
    };

    this.client = new Mistral({ apiKey: this.config.apiKey });
    this.dataSources = dataSources;
  }

  /**
   * Main entry point - analyze a trading query with tool calling
   */
  async analyze(query: string, context?: Record<string, any>): Promise<{
    analysis: string;
    toolsUsed: string[];
    rawData: Record<string, any>;
    tokenUsage: { input: number; output: number };
  }> {
    const startTime = Date.now();
    const toolsUsed: string[] = [];
    const rawData: Record<string, any> = {};
    let totalInputTokens = 0;
    let totalOutputTokens = 0;

    // Build system prompt
    const systemPrompt = this.buildSystemPrompt(context);

    // Initial message
    let messages: any[] = [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: query }
    ];

    // Tool calling loop
    let round = 0;
    while (round < this.config.maxToolRounds) {
      round++;

      const response = await this.client.chat.complete({
        model: this.config.model,
        temperature: this.config.temperature,
        maxTokens: this.config.maxTokens,
        messages,
        tools: ATLAS_TOOLS,
        toolChoice: 'auto'
      });

      const choice = response.choices?.[0];
      if (!choice) throw new Error('No response from Mistral');

      // Track token usage
      totalInputTokens += response.usage?.promptTokens ?? 0;
      totalOutputTokens += response.usage?.completionTokens ?? 0;

      const assistantMessage = choice.message;
      messages.push(assistantMessage);

      // Check if we have tool calls
      const toolCalls = assistantMessage.toolCalls;

      if (!toolCalls || toolCalls.length === 0) {
        // No more tool calls - we have our final answer
        this.emit('complete', {
          duration: Date.now() - startTime,
          rounds: round,
          toolsUsed
        });

        return {
          analysis: assistantMessage.content as string,
          toolsUsed,
          rawData,
          tokenUsage: {
            input: totalInputTokens,
            output: totalOutputTokens
          }
        };
      }

      // Execute tool calls in parallel
      this.emit('tools:calling', {
        round,
        tools: toolCalls.map((tc: ToolCall) => tc.function.name)
      });

      const toolResults = await Promise.all(
        toolCalls.map(async (toolCall: ToolCall) => {
          const name = toolCall.function.name;
          const args = JSON.parse(toolCall.function.arguments);

          toolsUsed.push(name);

          try {
            const result = await this.executeTool(name, args);
            rawData[name] = result;

            this.emit('tool:success', { name, args, resultSize: JSON.stringify(result).length });

            return {
              role: 'tool' as const,
              toolCallId: toolCall.id,
              content: JSON.stringify(result)
            };
          } catch (error) {
            this.emit('tool:error', { name, args, error });

            return {
              role: 'tool' as const,
              toolCallId: toolCall.id,
              content: JSON.stringify({
                error: error instanceof Error ? error.message : 'Unknown error',
                fallback: `Failed to fetch ${name}. Consider alternative data.`
              })
            };
          }
        })
      );

      // Add tool results to messages
      messages.push(...toolResults);
    }

    throw new Error(`Exceeded max tool rounds (${this.config.maxToolRounds})`);
  }

  /**
   * Execute a single tool call
   */
  private async executeTool(name: string, args: Record<string, any>): Promise<any> {
    // Check cache first
    const cacheKey = `${name}:${JSON.stringify(args)}`;
    const cached = this.toolCallCache.get(cacheKey);

    if (cached && Date.now() - cached.timestamp < this.cacheTTL) {
      this.emit('tool:cached', { name });
      return cached.result;
    }

    let result: any;

    switch (name) {
      case 'get_derivatives_data':
        result = await this.dataSources.coinglass.getDerivatives(
          args.symbol,
          args.metrics
        );
        break;

      case 'get_social_sentiment':
        result = await this.dataSources.lunarcrush.getSentiment(
          args.symbol,
          args.timeframe,
          args.include_influencers
        );
        break;

      case 'get_whale_activity':
        result = await this.dataSources.nansen.getWhaleActivity(
          args.symbol,
          args.activity_type,
          args.timeframe
        );
        break;

      case 'get_technical_indicators':
        result = await this.dataSources.technical.calculate(
          args.symbol,
          args.timeframe,
          args.indicators
        );
        break;

      case 'get_market_context':
        result = await this.getMarketContext(
          args.include_historical,
          args.include_correlations
        );
        break;

      case 'search_knowledge_base':
        result = await this.dataSources.rag.search(
          args.query,
          args.category,
          args.top_k ?? 5
        );
        break;

      case 'find_similar_setups':
        result = await this.dataSources.rag.findSimilar(
          args.conditions,
          args.min_similarity ?? 0.7
        );
        break;

      case 'calculate_position':
        result = this.calculatePosition(
          args.confluence_score,
          args.direction,
          args.entry_price,
          args.stop_loss_price
        );
        break;

      default:
        throw new Error(`Unknown tool: ${name}`);
    }

    // Cache the result
    this.toolCallCache.set(cacheKey, { result, timestamp: Date.now() });

    return result;
  }

  /**
   * Build system prompt with context
   */
  private buildSystemPrompt(context?: Record<string, any>): string {
    const basePrompt = `You are ATLAS, an autonomous cryptocurrency trading system's intelligence layer.

Your role is to orchestrate data gathering and analysis by calling the appropriate tools, then synthesizing the results into actionable trading intelligence.

## Tool Selection Guidelines

For SIGNAL ANALYSIS (should I trade?):
1. ALWAYS call get_derivatives_data first - funding and OI are leading indicators
2. Call get_social_sentiment for crowd positioning
3. Call get_whale_activity for smart money confirmation  
4. Call get_technical_indicators for entry timing
5. Call get_market_context for regime awareness

For HISTORICAL CONTEXT:
- Call search_knowledge_base for relevant patterns
- Call find_similar_setups when conditions are unusual

For POSITION SIZING:
- Only call calculate_position AFTER gathering signal data
- Requires confluence_score from your analysis

## Analysis Framework

POLARIS confluence **v6.1** — 220-point ladder. Category ceilings (Σ = 220; never exceed a ceiling):
- Derivatives intelligence (**max 75 pts**): funding z-score, OI composite, liquidation imbalance/cascade, basis.
- Whale / on-chain flow (**max 65 pts**): whale in/out flows, exchange netflow, active-address z-score.
- Technical filter (**max 15 pts**): ADX trend regime and Bollinger boundary context only — RSI/MACD/volume do **not** score in this pillar.
- Social/sentiment (**max 35 pts**; often 0 when gated): extremes only when a major news/sentiment event is active.
- Macro / regime context (**max 30 pts**): volatility regime, BTC correlation, fear/greed gate, BTC dominance.

Gate checks (align verbal reasoning with production gates):
- Funding: if |funding_rate| > 0.05%, apply the non-linear funding penalty per policy before finalizing derivatives contribution.
- Sentiment: if no major sentiment/news event is active, sentiment contribution = 0 pts.

Sizing tiers (raw score → PROMETHEUS ladder reference; overlays resize only, they do not change raw confluence):
- **180+**: 5% at 5x
- **150–179**: 3% at 3x
- **120–149**: 2% at 2x
- **<120**: SKIP / NO TRADE

## Response Format

After gathering data, provide:
1. Confluence breakdown by category with scores
2. Key supporting/opposing factors
3. Clear recommendation (LONG/SHORT/NO TRADE)
4. If trading: entry zone, stop loss, targets`;

    if (context) {
      return `${basePrompt}

## Current Context
${JSON.stringify(context, null, 2)}`;
    }

    return basePrompt;
  }

  /**
   * Get market context (Fear & Greed, dominance, etc.)
   */
  private async getMarketContext(
    includeHistorical?: boolean,
    includeCorrelations?: boolean
  ): Promise<any> {
    // This would integrate with your existing market context service
    // Placeholder implementation
    return {
      fear_greed: {
        value: 65,
        classification: 'Greed',
        previous_day: 58,
        previous_week: 45
      },
      btc_dominance: 52.3,
      total_market_cap: 2.8e12,
      regime: 'risk_on',
      ...(includeHistorical && {
        historical: {
          fear_greed_7d_avg: 55,
          dominance_change_7d: -1.2
        }
      }),
      ...(includeCorrelations && {
        correlations: {
          btc_sp500: 0.65,
          btc_gold: 0.12,
          btc_dxy: -0.45
        }
      })
    };
  }

  /**
   * Calculate position size based on confluence
   */
  private calculatePosition(
    confluenceScore: number,
    direction: 'long' | 'short',
    entryPrice?: number,
    stopLossPrice?: number
  ): any {
    const accountBalance = 5000; // Your capital

    // Position size based on confluence (v6.1 tiers — match backend/confluence_scorer.py)
    let positionPercent: number;
    if (confluenceScore >= 180) {
      positionPercent = 5;
    } else if (confluenceScore >= 150) {
      positionPercent = 3;
    } else if (confluenceScore >= 120) {
      positionPercent = 2;
    } else {
      return {
        recommendation: 'NO_TRADE',
        reason: `Confluence score ${confluenceScore} below minimum threshold of 120`
      };
    }

    const positionSizeUsd = accountBalance * (positionPercent / 100);

    const result: any = {
      confluence_score: confluenceScore,
      direction,
      position_percent: positionPercent,
      position_size_usd: positionSizeUsd,
      account_balance: accountBalance
    };

    // Calculate risk if stop loss provided
    if (entryPrice && stopLossPrice) {
      const riskPercent = Math.abs((entryPrice - stopLossPrice) / entryPrice) * 100;
      const riskUsd = positionSizeUsd * (riskPercent / 100);
      const riskOfAccount = (riskUsd / accountBalance) * 100;

      result.entry_price = entryPrice;
      result.stop_loss = stopLossPrice;
      result.risk_percent = riskPercent.toFixed(2);
      result.risk_usd = riskUsd.toFixed(2);
      result.risk_of_account = riskOfAccount.toFixed(2);

      // Warn if risk exceeds 2% of account
      if (riskOfAccount > 2) {
        result.warning = `Risk of ${riskOfAccount.toFixed(1)}% exceeds 2% max. Consider tighter stop or smaller position.`;
      }
    }

    return result;
  }

  /**
   * Clear tool cache
   */
  clearCache(): void {
    this.toolCallCache.clear();
  }

  /**
   * Get cache stats
   */
  getCacheStats(): { size: number; entries: string[] } {
    return {
      size: this.toolCallCache.size,
      entries: Array.from(this.toolCallCache.keys())
    };
  }
}

// ============================================================
// Exports
// ============================================================

export { ATLAS_TOOLS };
export type { Tool, ToolCall, ToolResult, MistralConfig };
