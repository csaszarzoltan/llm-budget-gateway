# Llm-budget-gateway Engineering Standards

> **Minden agent kötelezően olvassa el** a kódírás előtt (worker prompt hivatkozik rá).
> Ez a fájl a projekt "modern, felhasználóbarát, megbízható" definíciója —
> ami itt szerepel, az **ellenőrizhető követelmény**, nem ízlés.

## 1. Felhasználói felület / UX

- [ ] Minden felhasználói hibaüzenet magyar nyelven, érthető, cselekvésre vezető szöveggel jelenik meg
- [ ] Minden űrlap validációja valós időben (submit előtt) és a submit után is jelzi a hibát
- [ ] Minden gombnak/ikonnak van hozzáférhető neve (aria-label / title)
- [ ] A loading állapotok minden async műveletnél megjelennek (spinner/skeleton)
- [ ] A hibák soha nem "csendben" nyelődnek el — a felhasználó mindig kap visszajelzést
- [ ] Responsive: a fő felhasználói utak mobil nézetben is használhatók

## 2. API / Backend

- [ ] Minden API végpont hibája strukturált JSON: `{ "error": { "code": ..., "message": ... } }`
- [ ] Minden külső hívás (LLM, HTTP, DB) rendelkezik timeout-tal és hiba-kezeléssel
- [ ] Sose logolunk titkokat (API kulcs, jelszó, token)
- [ ] Minden változást igénylő végpont validálja a bemenetet (Pydantic/séma)
- [ ] Idempotencia: az ismételt kérések nem okoznak duplikált mellékhatást

## 3. Adat / Perzisztencia

- [ ] Minden séma-változás migrációval érkezik (nem kézi DDL)
- [ ] A kritikus írások tranzakcióban futnak (commit/rollback)
- [ ] A személyes adatok (PII) soha nem kerülnek logba / analitikába nyersen

## 4. Kódminőség

- [ ] TDD: minden új viselkedéshez előbb piros teszt, aztán implementáció
- [ ] Nincs dead code, kommentezett kód, debug print
- [ ] A függvények/modulok nevei a szándékot írják le, nem az implementációt
- [ ] DRY: az ismétlődő logika kiemelve, nem copy-paste
- [ ] A típusok explicit (Python: type hints; TS: strict)


### 4.1 Agent-barát kódolás (METH-COD-001…008)

> **Miért kell?** Az LLM-ek kontextusablakban dolgoznak — minden olvastatott token
> költséges. Ezek a szabályok azt érik el, hogy a kód **önmagát dokumentálja**, és
> egy agent **egy docstringből** tudja, hogy a függvény mit csinál, miért így van
> megírva, és mik az edge-case-ei — anélkül, hogy végigolvasná a implementációt.
> A teljes definíció: `docs/METHODOLOGY.md` → §15.8.

| rule_id | Szabály | Gyakorlati tipp |
|---------|---------|-----------------|
| `METH-COD-001` | **Rationale-kötelezettség:** minden nem-triviális függvény docstring tartalmazza a célt (üzleti nyelven) + a választott megoldás **indoklását** + az **elvetett alternatívát** (ha volt) | `"""Calculate customer discount.\n\nApplied to orders > $100, using tiered logic instead of flat-rate\n(benchmark: flat 10% over-discounted low-tier customers by 15%).\nAlternative considered: volume-based — rejected, too complex for MVP.\n"""` |
| `METH-COD-002` | **Modul-összefoglaló:** minden modul tetején 1–2 sorban a szerepe, fő bejárati függvényei, és kapcsolódó modulok | `# Handles outbound email delivery. Entry: send_welcome_email().\n# Depends on: services/user.py, shared/templates/` |
| `METH-COD-003` | **Edge-case + hiba-útvonal a docstringben:** mit ad vissza / dob hibára, és mik a határesetek | `"""Returns dict or None if not found. Raises ValueError if input is empty."""` |
| `METH-COD-004` | **Komplexitás-korlát:** radon E feletti függvényt szétválasztani kis egységekre, mindegyik kap docstringet | cc=171 → 3 cc≈5 részfüggvény, mindegyikkel magyarázat |
| `METH-COD-005` | **Refactor = döntés-napló:** ADR (docs/decisions/) dokumentálja, mi változott, miért, mi NEM változott, alternatívák | `docs/decisions/refactor-<DATE>.md` — NEM commit message |
| `METH-COD-006` | **Komment csak a MIÉRT-re:** `what`-kommentek (pl. `# increment counter`) tilosak — zaj | `# accounts for the 2024-11 timezone bug (see GH #142)` ✓ |
| `METH-COD-007` | **Elnevezés = szándék, nem implementáció:** `calculate_discount`, ne `loop_items_and_multiply`; `customer_balance`, ne `dict` | nevek "olvasd el és értsd" kell, hogy |
| `METH-COD-008` | **Típus-annotáció kötelező:** minden paraméter + visszatérési érték annotált; `mypy`/`pyright` gate | `def load_projects_config(path: Path) -> dict[str, ProjectConfig]:` |

**Enforcement:** a `mypy`/`pyright` és a `ruff` (docstring linting) a CI-ben ellenőrzi.
Egy függvény, amelynek nincs rationale docstring, vagy cc > E — **blokkolja a merge-t**.
## 5. Tesztelés

- [ ] A teljes suite zöld (0 failed) — a pre-existing hibák deselectelve a known-fail listából
- [ ] Minden javított hiba kap regressziós tesztet (lásd docs/decisions/)
- [ ] A kritikus felhasználói utak lefedettek (happy path + edge + error)

## 6. Biztonság

- [ ] Nincs hardcodeolt titok a repóban (.env a gitignore-ban)
- [ ] A bemenetek soha nem kerülnek közvetlenül SQL-be / shell-be (paraméterezés)
- [ ] A hozzáférések minimáljogosultság elvén működnek
- [ ] A függőségek verziója pinelve (pyproject.toml / package.json)

---

## Kapcsolódó fájlok

- `docs/decisions/` — javított hibák és döntések (anti-minták + helyes minták)
- `docs/specs/` — feature specifikációk (kanonikus követelmény)
- `shared/templates/task-contract.md` — task body kontraktus
