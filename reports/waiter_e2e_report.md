# Waiter Single-Flow E2E (full phase)

API: ASR=assemblyai_realtime | LLM=gemini_tools | TTS=cartesia_websocket
Case: recommend -> those-two -> 86-squid -> seabass -> morning-glory -> place

| turn | intent | asr_ms | ttfb_ms | e2e_ms | tools | basket_after |
|------|--------|-------:|--------:|-------:|-------|--------------|
| 1 | recommend | 500.19 | 17860.78 | 19232.23 | recommend_dishes | - |
| 2 | those-two | 580.72 | 25863.29 | 27120.31 | add_items_from_mention | Pomelo Salad with Shrimp, Lemongrass Chicken |
| 3 | 86-squid | 535.45 | 28230.36 | 28684.52 | search_menu, add_item | Pomelo Salad with Shrimp, Lemongrass Chicken, Crispy Squid |
| 4 | sub-seabass | 549.2 | 46511.13 | 47639.66 | search_menu, add_item | Pomelo Salad with Shrimp, Lemongrass Chicken, Crispy Squid, Grilled Seabass |
| 5 | add-morning-glory | 825.2 | 85967.16 | 86525.06 | search_menu, add_item | Pomelo Salad with Shrimp, Lemongrass Chicken, Crispy Squid, Grilled Seabass, Stir-fried Morning Glory |
| 6 | place | 450.56 | 53087.39 | 54130.08 | readback, place_order | Pomelo Salad with Shrimp, Lemongrass Chicken, Crispy Squid, Grilled Seabass, Stir-fried Morning Glory |

- accuracy_name_ok=True, total_ok=False, final_total=$46.0 (expected $36.5)
- all_replies_clean=True
- gemini calls est=16 in 276.3s = 3.474/min (cap 15, under=True)