/**
 * Type-safe contract for the /ws/realtime bi-directional streaming endpoint.
 */

export interface BasketItem {
  sku: string;
  name: string;
  price: number;
  quantity: number;
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
  | {
      type: "turn_complete";
      answer: string;
      turn_count?: number;
      session_ended?: boolean;
      ttfb_ms?: number;
      e2e_turn_ms?: number;
    }
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
