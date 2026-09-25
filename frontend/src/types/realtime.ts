/**
 * Type-safe contract for the /ws/realtime bi-directional streaming endpoint.
 */

export interface BasketItem {
  sku: string;
  name: string;
  price: number;
  quantity: number;
}

export interface TurnTimingMs {
  rag_ms?: number;
  ttfb_ms?: number;
  e2e_turn_ms?: number;
  stt_finalize_ms?: number;
  llm_ttft_ms?: number;
  llm_total_ms?: number;
  tts_ttfb_ms?: number;
  tts_total_ms?: number;
  perceived_ttfb_ms?: number;
}

export interface TurnProvider {
  asr?: string;
  llm?: string;
  tts?: string;
}

export interface TurnChunk {
  chunk_id: string;
  text?: string;
  source?: string;
}

export interface TurnToolCall {
  tool: string;
  args: Record<string, any>;
}

export interface TurnTelemetry {
  turn_id?: string;
  session_id?: string;
  query?: string;
  transcript?: string;
  answer?: string;
  timestamp?: string;
  profile?: string;
  provider?: TurnProvider;
  timings_ms?: TurnTimingMs;
  ttfb_ms?: number;
  e2e_turn_ms?: number;
  chunks?: TurnChunk[];
  chunk_ids?: string[];
  tool_calls?: TurnToolCall[];
  cache?: {
    retrieval_cache_hit?: boolean;
    spoken_cache_hit?: boolean;
    n_tool_calls?: number;
    stats?: any;
  };
  turn_count?: number;
  session_ended?: boolean;
  basket?: any;
  phase?: number;
}

export type ServerRealtimeMessage =
  | {
      type: "session_ready";
      session_id: string;
      profile?: string;
      has_assemblyai: boolean;
      has_cartesia: boolean;
    }
  | {
      type: "speech_started";
    }
  | {
      type: "interim_transcript";
      text: string;
    }
  | {
      type: "final_transcript";
      text: string;
    }
  | {
      type: "barge_in";
      reason?: string;
    }
  | {
      type: "audio_chunk";
      pcm_b64: string;
      ttfb_ms?: number;
    }
  | ({
      type: "turn_complete";
      answer: string;
      turn_count?: number;
      session_ended?: boolean;
      ttfb_ms?: number;
      e2e_turn_ms?: number;
    } & Partial<TurnTelemetry>)
  | {
      type: "waiter_action";
      action: string;
      item?: string;
      basket?: BasketItem[];
      total?: number;
    }
  | {
      type: "error";
      message?: string;
      detail?: string;
    };

export type ClientRealtimeCommand =
  | { command: "endpoint" }
  | { command: "barge_in" }
  | { command: "reset" };

/* ------------------------------------------------------------------ */
/* V2 runtime contract (backend/app/main.py): what /ws/realtime sends  */
/* today. The V1 types above stay for the Logs view's telemetry cards. */
/* ------------------------------------------------------------------ */

export interface V2BasketLine {
  sku: string;
  name: string;
  quantity: number;
  unit_price: number;
  line_total: number;
  modifiers?: string[];
}

export interface V2Order {
  order_id: string;
  table_id: string;
  status: string;
  placed?: boolean;
  current_revision: number;
  basket: V2BasketLine[];
  total: number;
  allergies?: string[];
  created_at?: string;
  updated_at?: string;
}

export type V2ServerMessage =
  | { type: "session_ready"; table_id: string; order: V2Order | null; asr_status: string; has_tts: boolean;
      providers?: { asr?: string; llm?: string; tts?: string } }
  | { type: "provider_ready"; provider: string }
  | { type: "provider_unavailable"; provider: string; message?: string }
  | { type: "interim_transcript"; text: string; language_code?: string }
  | { type: "final_transcript"; text: string; language_code?: string }
  | ({ type: "workflow_update"; status: string; response_text?: string; source?: string } & Partial<V2Order>)
  | { type: "audio_chunk"; pcm_b64: string; sample_rate: number; ttfb_ms?: number | null }
  | { type: "turn_complete"; pipeline_ms?: number | null; voice_ttfb_ms?: number | null }
  | { type: "barge_in"; reason?: string }
  | { type: "error"; message?: string };
