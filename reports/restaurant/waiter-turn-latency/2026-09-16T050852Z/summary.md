# Live Voice Waiter Benchmark Report — 2026-09-16 12:08:52

## 1. Executive Summary
- **Overall Response Accuracy**: **100.0%** (PASS)
- **Speed vs Yesterday**: TTFB **+86.4%** | E2E **+81.9%** (FASTER)
- **Gemini Stability**: **STABLE** (429 Errors: 0, Timeouts: 0, RPM: 8.09/15)

## 2. Turn-by-Turn Telemetry & Function Calling
| Turn | Intent | Functions Used | TTFB (Today) | TTFB (Yest) | E2E (Today) | E2E (Yest) | Speedup |
|:---:|:---|:---|---:|---:|---:|---:|---:|
| 1 | `recommend` | `(none)` | 5418.12 ms | 17860.78 ms | 8474.35 ms | 19232.23 ms | +55.9% |
| 2 | `those-two` | `add_items_from_mention` | 7193.89 ms | 25863.29 ms | 8996.88 ms | 27120.31 ms | +66.8% |
| 3 | `86-squid` | `(none)` | 4711.55 ms | 28230.36 ms | 7386.01 ms | 28684.52 ms | +74.3% |
| 4 | `sub-seabass` | `order_items` | 6134.21 ms | 46511.13 ms | 8439.42 ms | 47639.66 ms | +82.3% |
| 5 | `side-morning-glory` | `order_items` | 5868.55 ms | 85967.16 ms | 7493.04 ms | 86525.06 ms | +91.3% |
| 6 | `place` | `place_order` | 5659.16 ms | 53087.39 ms | 6788.35 ms | 54130.08 ms | +87.5% |

## 3. Responses & Basket Mutations
### Turn 1: [recommend]
- **User**: *"What would you recommend for a mild couple?"*
- **AI Spoken**: *"For a mild couple, I definitely recommend our refreshing Pomelo Salad with Shrimp and our flavorful Lemongrass Chicken."*
- **Functions Called**: `[]`
- **Basket After Turn**: `[]`

### Turn 2: [those-two]
- **User**: *"We'll take those two please"*
- **AI Spoken**: *"I've added the lemongrass chicken and pomelo salad, bringing your total to fifteen fifty."*
- **Functions Called**: `['add_items_from_mention']`
- **Basket After Turn**: `['Lemongrass Chicken', 'Pomelo Salad with Shrimp']`

### Turn 3: [86-squid]
- **User**: *"I'd like the crispy squid too"*
- **AI Spoken**: *"Ah, the crispy squid is actually sold out tonight, but I can get you the crispy spring rolls or fried banana instead!"*
- **Functions Called**: `[]`
- **Basket After Turn**: `['Lemongrass Chicken', 'Pomelo Salad with Shrimp']`

### Turn 4: [sub-seabass]
- **User**: *"Okay, make it a grilled seabass instead"*
- **AI Spoken**: *"I've added the grilled seabass, bringing your total to $31.50 for the lemongrass chicken, pomelo salad with shrimp, and grilled seabass."*
- **Functions Called**: `['order_items']`
- **Basket After Turn**: `['Lemongrass Chicken', 'Pomelo Salad with Shrimp', 'Grilled Seabass']`

### Turn 5: [side-morning-glory]
- **User**: *"And stir-fried morning glory on the side"*
- **AI Spoken**: *"I have added the stir-fried morning glory, bringing your total to 36 dollars and 50 cents. Anything else?"*
- **Functions Called**: `['order_items']`
- **Basket After Turn**: `['Lemongrass Chicken', 'Pomelo Salad with Shrimp', 'Grilled Seabass', 'Stir-fried Morning Glory']`

### Turn 6: [place]
- **User**: *"That's all, please place the order"*
- **AI Spoken**: *"Your order is placed. Total is thirty-six fifty."*
- **Functions Called**: `['place_order']`
- **Basket After Turn**: `['Lemongrass Chicken', 'Pomelo Salad with Shrimp', 'Grilled Seabass', 'Stir-fried Morning Glory']`
