# Stabilitási mérés + javítás — 2026-09-22

Folytatás: `docs/STABILITY-PLAN-2026-09-19.md` (hat pont, mind lezárva).
Ez a dokumentum a **mérés** eredményét és az abból jött **egyetlen új javítást** rögzíti.

## 1. Előtte / utána mérés (`cost_records`, élő adat)

| ablak | kérés | hiba | hibaarány | 502 | 429/500/403 | p50 / p90 / p99 |
|---|---|---|---|---|---|---|
| 09-12 → 09-16 | 3796 | 0 | 0.00% | 0 | 0 / 0 / 0 | 4.6s / 37.5s / 124s |
| 09-16 → 09-19 07:54 | 3518 | 104 | 2.96% | 12 | 58 / 24 / 8 | 5.2s / 36.4s / 103s |
| 09-19 07:54 → 09-22 | 3712 | 60 | **1.62%** | **60** | **0 / 0 / 0** | 4.6s / 34.9s / 134s |

Amit a javítások elintéztek: a **429-es limitek (58 → 0)**, a **generikus 500-asok (24 → 0)**
és a 403/404-esek (10 → 0) teljesen eltűntek. A hibaarány **2.96% → 1.62%**.

Ami romlott: a **route-szintű 502-k 12 → 60**. Napi bontás: 09-19: 29, 09-20: 30,
09-21: 1, azóta 0 (utolsó: 09-21 04:28).

## 2. A 502-k gyökér-oka: upstream timeout, nem HTTP-hiba

Négy 502-es kérés láncának rekonstrukciója a logból — mind ugyanaz:

```
route=hermes-default candidates=@opencode-go/muse-spark-1.3-contributor,...
  model=@opencode-go/muse-spark-1.3-contributor timed out after 90s
  chain budget 115.0s spent (180.0s) skipping @opencode-go/deepseek-v4.1-flash
  chain budget 115.0s spent (180.0s) skipping @opencode-go2/muse-spark-1.3-contributor
  chain budget 115.0s spent (180.0s) skipping @opencode-go/glm-5.3-flash
  chain budget 115.0s spent (180.0s) skipping @openrouter/qwen/qwen3.8-27b:free
  model=@openrouter/z-ai/glm-5.3-flash timeout retry 1/1     ← a farok megkapja a reserve-ot
  → 502
```

- `upstream hiba` (HTTP-státuszos bukás) a 09-19 07:54 utáni logban: **0**.
- `timed out after`: **63** (muse-spark go 30, go2 17, glm-5.3 6, deepseek 6, qwen 3, deepinfra 1).
- A farok-reserve (`_LAST_CANDIDATE_RESERVE`) **bizonyítottan működik**: az utolsó jelölt
  30s-ot kap és megpróbálkozik (korábban 0.01s volt).

A kiváltó ok a kontextusméret: a sikeres kérések **p50 = 54 727, p75 = 90 882,
p90 = 139 887, p99 = 193 192 token** (max 248 401). A kérések 21%-a 100K token felett van —
egy 140K-s prompt 90s alatt gyakran nem fut le. **A timeout tehát kapacitás-jel, nem hibajel.**

## 3. Az amplifikátor: a timeout egy órára parkolt

A timeout-ág **megkerülte** a `_cooldown_decision` blame-osztályozást:

```python
cooldown_seconds = int(cooldown_info.get("seconds", 3600))  # a target TELJES cooldownja
count_strike    = cooldown_info.get("dynamic", True)        # + strike-eszkaláció
```

A route egyetlen targetjének sincs explicit cooldownja, tehát a default élt:
**egyetlen timeout = 3600s parkolás + strike**. Mérve:

| modell | `skipped (cooldown ...)` |
|---|---|
| `@opencode-go/muse-spark-1.3-contributor` (primary) | **1068** |
| `@opencode-go/deepseek-v4.1-flash` | 243 |
| `@opencode-go2/muse-spark-1.3-contributor` | 55 |
| többi | ~111 |
| **összesen** | **1477** |

Vagyis **30 primary timeout → 1068 kihagyott kísérlet** 2,8 nap alatt. A lánc a gyengébb
tartalékokra csúszott, azok is timeoutoltak (és azok is parkolódtak) → **önmagát gerjesztő
hurok**, a végén 502-vel.

## 4. Javítás — `_TIMEOUT_COOLDOWN_SECONDS = 60`, strike nélkül

Ugyanaz az alak, mint a tranziens-5xx floor (`b3779a3`):

- timeout → `min(base, 60)` másodperc, **`count_strike = False`** (nem mászik a létrán);
- `dynamic: false` (explicit operátori beállítás) → **marad a megadott idő**.

Egy lassú kérés így legfeljebb **egy percet** vesz el a legjobb modellből egy óra helyett,
és a hurok megszakad. A parkolás maga megmarad (nem verjük folyamatosan a lassú providert).

Tesztek: `TestTimeoutCooldownIsShortAndDoesNotEscalate` (2 teszt) — 60s + `count_strike=False`,
illetve a statikus target 3600s-ja érintetlen. Teljes suite: **1379 passed**.

## 5. Nyitva maradt

- Az időtúllépés **magát** nem oldja meg: 140K-s promptra 90s kevés lehet. A hosszú távú
  válasz vagy nagyobb target-timeout (a 115s keret és a kliens 120s-os türelmének határán),
  vagy a kontextus csökkentése a kliens oldalán.
- A `@openrouter/qwen/qwen3.8-27b:free` és a `@openrouter/z-ai/glm-5.3-flash` a farokban
  30s alatt nem tud nagy kontextust kiszolgálni — ha a farok szerepét tartósan ezek töltik be,
  érdemes lehet a reserve-ot 45s-ra emelni.
- A `receiptslens` klón 64 committal az origin mögött van (`.worktrees/*` piszkos) — külön
  kör, nem a gateway.
