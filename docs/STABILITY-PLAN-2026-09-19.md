# Gateway stabilitási terv — 2026-09-19

Élő dokumentum. Minden lépésnél jelölve: `[ ]` nyitott, `[x]` kész + commit/igazolás.

Kiindulás: a 2026-09-18/19-i hibafelmérés (8970 log-sor, 2 nap) és a `stability-audit`
referencia. Minden állítás mögött mérés van, lásd a „Bizonyíték" sorokat.

---

## 1. Cooldown-blame osztályozás

**Probléma.** A `fallback_statuses` az összes target `on_status_codes` uniója, amiben
szerepel a **400** is. Egy kérés-szintű hiba ezért végigjárja a teljes láncot, és
minden modell **teljes cooldownt + strike-eszkalációt** kap — miközben a hiba oka a
kérés, nem a provider. Egyetlen hibás kérés így az egész láncot parkolja.

**Bizonyíték.** `deepseek-v4-flash` strikes=4 és `deepseek-v4.1-flash` strikes=1
mindkettő 400 „No tool output found" indoklással; a cooldown-tábla mai tartalmából
9 bejegyzés kérés-szintű (400/401/403/404). A lánc nagy része egyszerre volt
cooldownban a `_admin/probe-route` mérés szerint.

**Megoldás.** Új `_cooldown_decision(status_code, cooldown_info, body)` segéd, és
mindkét hívási hely (státusz-ág + kivétel-ág) ezt használja:

| kategória | státuszok | cooldown | strike |
|---|---|---|---|
| kérés-szintű | 400,405,408,409,413,415,422,424,425,428 | nincs | nem |
| modell-szintű, végleges | 401/403/404 + végleges-üzenet minta | terminális (hosszú) | nem |
| modell-szintű, átmeneti | 401/403/404 egyébként | fix, nem eszkalál | nem |
| provider 429/502/503/504 | — | ≤60s | nem |
| provider egyéb 5xx | 500,501,505,… | teljes | igen |

A failover **nem** változik: a kérés-szintű státuszok továbbra is fallback-élhetőek
(nagy kontextusú kérés megtalálhatja a nagyobb ablakú modellt) — csak a büntetés marad el.

**Bizonyíték a pattern-alapú terminális jelöléshez.** `403 Key limit exceeded (daily
limit)` → **átmeneti** (naponta resetel), ezért tilos vakon terminálisnak jelölni.
Végleges csak akkor, ha a body konkrétan kimondja: „is not supported",
„unavailable for free", „model_not_found", „does not exist", „retired".

- [x] kód + tesztek + commit
- [x] igazolás: teszt-osztályonként 1-1 eset

## 2. Timeout-illesztés a kliens türelméhez

**Probléma.** A cél-timeout 90s, a `go/muse-spark`-on `retries=2` → **270s egyetlen
modellen**; a chain budget 150s; a Hermes oldali `llm-gw` timeout **120s**. A gateway
túlélheti a klienst: a kapott választ senki nem látja.

**Bizonyíték (7 nap, sikeres kérések latenciája).**

| modell | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| go2/muse-spark-1.3 | 3800 | 5.7s | 41.6s | 129.7s | 206s |
| go/muse-spark-1.3 | 1202 | 4.3s | 38.3s | 120.3s | 259s |
| go/deepseek-v4.1-flash | 22 | 9.3s | 24.1s | 61s | 61s |

A 90s cél-timeout helyes (p90 ≈ 40s, a farok vágása szándékos). A **retry** a
költséges: egy p99≈120s modellt újrapróbálni dupla várakozás haszon nélkül.
2 nap alatt 17× „timed out after 90s" (go2/muse), 50× timeout-retry.

**Megoldás.**
- `go/muse-spark-1.3-contributor` `retries` 2 → 1 (a legrosszabb eset 270s → 180s).
- `GATEWAY_ROUTE_TIMEOUT_BUDGET` 150 → **115s** (a kliens 120s-a alatt), így a
  gateway mindig válaszol, mielőtt a kliens feladja. A p90 (40s) belefér, tehát a
  nagy kontextusú kérések sértetlenek.

- [x] retries 2→1 (product.db, publish)
- [x] drop-in env 150→115 (`/etc/systemd/system/llm-budget-gateway.service.d/timeout.conf`)

## 3. Provider-diverzitás a láncban

**Probléma.** Az 5 engedélyezett targetből **4 ugyanaz a szállító** (opencode-go/go2).
Egy szállító kvótája vagy kiesése az egész láncot viszi.

**Bizonyíték.** A `_admin/probe-route` mérés mind az ötöt cooldownban/429-en találta
egyszerre; a nem-opencode target (qwen) volt az egyetlen, ami nem cooldownban volt.

**Megoldás.** A letiltott, más szállítós tartalékok állapotának ellenőrzése után
(alapos indok kell a visszakapcsoláshoz) visszaengedés **alacsony prioritáson**:
`@openrouter/z-ai/glm-5.3-flash`, `@xiaomi/mimo-v2.5`, `@deepinfra/deepseek-…`.

- [x] élő állapot-ellenőrzés targetenként
- [x] ami egészséges → enabled=true, magas priority, publish
- [x] igazolás: probe-route mutatja a megnövelt jelöltszámot

## 4. Log-rotáció

**Probléma.** `gateway.log` = **136 MB**, egy fájlban, rotáció nélkül; a `grep`-ek
másodpercekig futnak rajta, és előbb-utóbb betölti a lemezt.

**Megoldás.** `/etc/logrotate.d/llm-budget-gateway`: méret-alapú (50M), 5 generáció,
tömörítés, `copytruncate` (a fájlt egy gyermekprocessz írja nyitott fd-del, ezért kell).

- [x] logrotate konfig + `logrotate -d` száraz futás igazolás

## 5. Watchdog az elakadt workerekre

**Probléma.** Nincs semmi, ami egy megakadt processzt észlelne. `NRestarts=0` — eddig
nem kellett, de nincs háló.

**Megoldás.** systemd timer + script: 30s-enként `curl` a 8000/8013 `/health`-re;
3 egymást követő hiba után `systemctl restart`, de legfeljebb 10 percenként egyszer
(nehogy egy lassú indulás alatt újraindítási hurok legyen).

- [x] script + unit fájlok + enable
- [x] igazolás: száraz futás naplózza a döntést

## 6. Keményítés

**6a. SQLite `busy_timeout`.** A kapcsolatok `journal_mode=WAL`-t állítanak, de
`busy_timeout`-ot nem — 4 worker mellett ütközésnél azonnali `database is locked`.
Utolsó előfordulás: **aug. 6.** (utolsó 20 000 sorban 0), tehát **lappangó** kockázat,
nem élő hiba. Fix: `PRAGMA busy_timeout=5000` + `synchronous=NORMAL`.

**6b. `_model_known` 404.** A guard `pricing_overrides` / `fallback_configs` /
`litellm.model_cost` hármasra támaszkodik — egy újonnan bekötött, de az árlistában még
nem szereplő modell **téves 404-et** kap.

- [x] 6a kód + teszt
- [x] 6b kód + teszt
- [x] commit

## Végrehajtás — eredmény (2026-09-19)

| # | pont | eredmény | commit / igazolás |
|---|---|---|---|
| 1 | cooldown-blame | `_cooldown_decision` mindkét ágon; kérés-szintű státusz **nulla** büntetés | `18e44a6`, +6 teszt |
| 2 | timeout | retries 2→1 (publish **v125**); budget 150→**115s** a drop-inben | élő env ellenőrizve |
| 3 | diverzitás | 3 egészséges tartalék visszakapcsolva (publish **v126**) | probe: `served_by: z-ai/glm-5.3-flash` |
| 4 | log-rotáció | logrotate (50M, 5 gen., copytruncate) | 143 MB → **10.3 MB**, írás folytatódik |
| 5 | watchdog | watchdog timer 30s, 3 hiba → restart, 10 perc plafon | mindkét ág dry-runnal igazolva |
| 6 | keményítés | pragmák + `knows_model` a 404-guardban | `58f2f97`, +4 teszt |

**Korrekció a tervhez (6a).** Az audit feltevése téves volt: a Python `sqlite3` **alapból
5 s** busy_timeout-ot állít be, tehát nem „azonali" a `database is locked`. A naplóban
talált előfordulások hetekkel korábbiak, az utolsó 20 000 sorban nulla. Ezért a pragma
**keményítés**, nem hiba javítása — 10 s explicit értékre emelve, indoklással a kódban.

**Nem módosult:** `@opencode-zen/muse-spark-1.3-contributor-free` (próbán HTTP 500 —
upstream abuse-gating, dokumentáltan javíthatatlan) és a 09:00–17:00 ablakos
duplikált `@xiaomi/mimo-v2.5` bejegyzés (ugyanaz a modell nem foglalhat két helyet a láncban).

## Élesítés

A kód-javítások (1, 6) és a timeout-env (2) **egyetlen restarttal** lépnek életbe.
A restartot a gateway processzén belülről nem lehet elvégezni — a pontos parancsot
a záró jelentés tartalmazza. A 3., 4., 5. pont restart nélkül vagy saját daemonnal él.
