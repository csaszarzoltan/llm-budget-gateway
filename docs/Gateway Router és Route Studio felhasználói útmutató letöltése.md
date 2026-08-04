# Gateway Router és Route Studio felhasználói útmutató

**Alkalmazás:** LLM Budget Gateway  
**Érintett felület:** Routes / Route Studio  
**Célközönség:** fejlesztők, platformüzemeltetők, FinOps-, biztonsági és AI-platform csapatok  
**Dokumentum célja:** bemutatni a gateway router működését, a route-ok életciklusát, valamint a biztonságos route-tervezés és üzemeltetés teljes folyamatát

---

## 1. Mi a Gateway Router?

A Gateway Router az alkalmazások és a külső AI-modellek közötti vezérlési réteg. Az alkalmazásnak nem kell közvetlenül egy konkrét szolgáltató vagy modell címét használnia. Ehelyett egy stabil route-nevet hív meg, a gateway pedig a route aktuális szabályai alapján választja ki a megfelelő modellt.

Például az alkalmazás ezt a stabil nevet használhatja:

```text
@route/support-global
```

A route mögött a Gateway Router döntheti el, hogy:

1. melyik legyen az elsődleges modell;
2. melyik modell legyen a fallback;
3. milyen hibák esetén történjen átváltás;
4. melyik modell használható egy adott régióban;
5. milyen képességeket kell tudnia a modellnek;
6. mekkora timeout és retry limit alkalmazható;
7. milyen költségkorlát mellett jogosult a modell;
8. milyen metadata-feltétel esetén választható egy ág;
9. a forgalom hány százaléka kerüljön egy canary modellhez;
10. melyik route-verzió legyen éles.

### Miért előnyös ez?

A kliensalkalmazás kódja változatlan maradhat akkor is, ha:

- modellt cserélsz;
- új szolgáltatót vezetsz be;
- fallbacket adsz hozzá;
- költségkorlátot módosítasz;
- regionális routingot vezetsz be;
- canary tesztet indítasz;
- visszaállsz egy korábbi route-verzióra.

A route ezért nem egyszerű modellalias. Egy verziózott, validálható és auditálható routing-szabályzat.

---

## 2. Alapfogalmak

### 2.1. Provider connection

A provider connection egy névvel ellátott, titkosított szolgáltatói kapcsolat. Tartalmazza például:

- a szolgáltató típusát;
- a Base URL-t;
- az API-kulcsot;
- a régiót;
- a felderített modelleket;
- a kompatibilitási és egészségi állapotot.

Példák:

```text
OpenAI Production
DeepInfra EU
Xiaomi MiMo Token Plan
Z.AI Coding Plan
Together AI Development
```

A route csak felderített, megfelelően beállított provider connectionhöz tartozó modellt használjon.

### 2.2. Model target

A model target a route egy végrehajtható célpontja. A target meghatározza:

- a provider connectiont és modellt;
- a prioritást;
- a routing módot;
- az időablakot;
- a timeoutot;
- a retry limitet;
- a szükséges képességeket;
- a fallbacket kiváltó HTTP-státuszkódokat;
- az opcionális feltételt;
- az opcionális forgalmi súlyt;
- az opcionális költségplafont.

### 2.3. Primary target

Az elsőként vizsgált, legmagasabb prioritású target. Normál esetben ez szolgálja ki a kérést.

### 2.4. Fallback target

A következő jogosult target, amelyet a gateway akkor próbál meg, ha az előző target:

- nem jogosult;
- nem elérhető;
- nem rendelkezik szükséges képességgel;
- túllépi az engedélyezett költségkeretet;
- nem felel meg a metadata-feltételnek;
- nem került bele a weighted routing bucketbe;
- konfigurált hibát vagy timeoutot ad vissza.

### 2.5. Draft

A route szerkeszthető változata. A draft módosítása nem változtatja meg automatikusan az éles forgalmat.

### 2.6. Live vagy published version

A route aktuálisan éles verziója. Az alkalmazások ezt a konfigurációt használják.

### 2.7. Validation

A route szerkezetének és policy-paramétereinek ellenőrzése. Publikálni csak a validált aktuális draftot szabad.

### 2.8. Simulation

Providerhívás nélküli döntési próba. Megmutatja, hogy egy meghatározott kérésnél melyik target lenne kiválasztva, és a többi miért esne ki.

### 2.9. Archive

A route visszaállítható, forgalomból kivont állapota. Az archiválás megtartja a verziókat és az auditbizonyítékot.

### 2.10. Permanent delete

Végleges törlés. Csak archivált és függőségektől mentes route esetén végezhető el, pontos névmegerősítéssel.

---

## 3. Felkészülés route létrehozása előtt

Route tervezése előtt végezd el az alábbi ellenőrzéseket.

### 3.1. Szolgáltató csatlakoztatása

1. Nyisd meg a **Providers** oldalt.
2. Válaszd a **Connect provider** műveletet.
3. Keresd ki a kívánt szolgáltatót.
4. Ellenőrizd az előre kitöltött Base URL-t.
5. Add meg a kapcsolat nevét és slugját.
6. Add meg az API- vagy subscription keyt.
7. Mentsd el a kapcsolatot.
8. Válaszd a **Test & sync models** műveletet.

A route szerkesztő csak a felderített modelleket kínálja fel. Ha nem jelenik meg egy modell, először szinkronizáld újra a provider connectiont.

### 3.2. Kompatibilitás ellenőrzése

A **Safety / Provider Compatibility Lab** segítségével ellenőrizd legalább azokat a képességeket, amelyeket a route megkövetel:

- chat completion;
- streaming;
- tool calling;
- structured output;
- embeddings;
- authentication;
- modellkatalógus elérése.

Ne feltételezd, hogy két OpenAI-kompatibilis szolgáltató minden paramétert azonosan támogat.

### 3.3. Üzleti követelmények összegyűjtése

Route létrehozása előtt válaszold meg:

- Melyik alkalmazás használja a route-ot?
- Mi az elsődleges cél: minőség, költség, latency vagy rendelkezésre állás?
- Szükséges-e EU vagy más regionális adatkezelés?
- Kell-e tool calling vagy structured output?
- Mekkora a megengedett request timeout?
- Hány retry fogadható el?
- Mi legyen a fallback sorrend?
- Mekkora költségnövekedés fogadható el fallback esetén?
- Szükséges-e canary vagy fokozatos rollout?
- Ki hagyhatja jóvá a production változást?

---

## 4. A Routes inventory használata

Nyisd meg a fő navigációban a **Routes** oldalt.

A felület minden route-nál megmutatja:

- a route nevét;
- a stabil aliast;
- az állapotot;
- a stratégiát;
- a targetek számát;
- a draft verziót;
- a live verziót;
- a readiness vagy health állapotot;
- az elérhető műveleteket.

### 4.1. Keresés

A **Search routes** mezőben route-névre vagy aliasra kereshetsz.

Példák:

```text
support
coding
@route/support-global
```

### 4.2. Státuszszűrés

A Status mezővel szűrhetsz például:

- minden route-ra;
- live route-okra;
- draftokra;
- archivált route-okra.

### 4.3. Archivált route-ok megjelenítése

Kapcsold be az **Archived routes** opciót. Az archivált route-ok nem jelennek meg az aktív operációs listában alapértelmezetten.

### 4.4. Kontextusmenü

A route sorának végén található `⋯` menüből érhető el:

- Edit draft;
- Duplicate;
- Archive;
- Restore;
- Delete permanently.

A veszélyes műveletek külön, piros stílusban jelennek meg.

---

## 5. Új route létrehozása

### 5.1. Kiindulási sablon választása

A Routes oldal sablonokat kínál:

- **Reliable fallback:** elsődleges modell és rendezett fallbackek;
- **Cost-aware:** költségtudatos elsődleges és olcsó fallback;
- **Regional:** regionális és időablakos routing;
- **Gradual rollout:** weighted canary bevezetés.

Ha egyszerű route-ot készítesz, indulj a Reliable fallback mintából.

### 5.2. Route neve

Használj stabil, funkcionális nevet.

Jó nevek:

```text
support-global
coding-assistant
invoice-extraction-eu
premium-chat
```

Kerüld a modellhez kötött neveket:

```text
gpt-4o-route
claude-main
```

A route tovább élhet akkor is, ha a mögöttes modellek lecserélődnek.

### 5.3. Elsődleges modell kiválasztása

A primary targethez olyan modellt válassz, amely:

- teljesíti a szükséges képességeket;
- megfelelő régióban fut;
- kompatibilitása frissen ellenőrzött;
- elfogadható latencyvel rendelkezik;
- belefér a költségkeretbe.

### 5.4. Első draft létrehozása

A route létrehozása után nyisd meg az **Open designer** művelettel a teljes Route Studiót.

---

## 6. A Route Studio felépítése

A Route Studio négy fő lapot tartalmaz:

- **Overview**
- **Flow**
- **Test**
- **Versions**

### 6.1. Fejléc

A fejlécben látható:

- visszalépés a route-listához;
- route-név;
- route-állapot;
- stabil alias;
- mentési állapot;
- Save draft;
- Review & publish.

A mentési állapot lehet:

```text
All changes saved
Unsaved changes
```

### 6.2. Flow lap

A vizuális route-folyamat három részből áll:

1. bal oldali building-block palette;
2. középső route canvas;
3. jobb oldali inspector.

---

## 7. A vizuális route-flow értelmezése

### 7.1. Start node

A route belépési pontja. A gateway ide kapja az OpenAI-kompatibilis kérést.

### 7.2. Model node

Egy végrehajtható modell-targetet jelöl. A node-on látható:

- modell alias;
- prioritás;
- timezone;
- primary vagy fallback szerep;
- timeout;
- retry limit.

### 7.3. Fallback edge

A piros, szaggatott kapcsolat azt jelenti, hogy az előző target sikertelensége vagy kizárása után a gateway a következő targetet vizsgálja.

### 7.4. Success edge

A zöld ág sikeres modellválasz után a végpontra vezet.

### 7.5. End node

A route befejezése. A gateway visszaadja a kiválasztott modell válaszát az alkalmazásnak.

---

## 8. Target hozzáadása és sorrendezése

### 8.1. Új fallback hozzáadása

A bal oldali palette-ben vagy a canvas tetején válaszd:

```text
+ Add target
```

Az új target a lánc végére kerül.

### 8.2. Target kijelölése

- kattints a node-ra; vagy
- Tab billentyűvel fókuszáld;
- Enter vagy Space segítségével válaszd ki.

A kiválasztott node kiemelt keretet kap.

### 8.3. Sorrend módosítása

Használd a target node alján található gombokat:

```text
Move up
Move down
```

Ez a módszer egérrel és billentyűzettel egyaránt működik.

A sorrend jelentése:

```text
1. primary target
2. első fallback
3. második fallback
4. harmadik fallback
```

A route prioritásai automatikusan frissülnek.

### 8.4. Target eltávolítása

Az inspector alján válaszd a **Remove target** műveletet.

Az utolsó megmaradt target nem törölhető, mert egy route-nak legalább egy célponttal rendelkeznie kell.

---

## 9. Target inspector részletes használata

## 9.1. Model

Válaszd ki a provider-szinkron során felderített gateway model aliast.

Példa:

```text
@deepinfra-prod/deepseek-ai/DeepSeek-V3
```

A provider slug és a model ID együtt egyértelművé teszi, melyik kapcsolatot használja a route.

## 9.2. Routing mode

### Ordered fallback

A target prioritási sorrendben kerül kiértékelésre.

Használd, ha:

- mindig ugyanazt a modellt szeretnéd elsőként;
- csak hiba esetén kell váltani;
- egyszerű, kiszámítható működés kell.

### Weighted split

A target csak a forgalom meghatározott részén jogosult.

Használd:

- canary bevezetéshez;
- A/B teszthez;
- fokozatos modellmigrációhoz;
- több provider közötti forgalommegosztáshoz.

### Conditional target

A target metadata-feltétel alapján választható.

Használd:

- premium és free felhasználók elkülönítésére;
- tenant-specifikus routinghoz;
- régió vagy alkalmazás szerinti eltéréshez;
- képességi vagy use-case alapú modellválasztáshoz.

## 9.3. Traffic weight

Weighted target esetén 0 és 100 közötti érték.

Példa:

```text
10
```

Ez azt jelenti, hogy a determinisztikus sticky bucketek körülbelül 10%-a jogosult a targetre.

A százalékos routing önmagában nem elég. Mindig legyen olyan fallback ág, amely a kimaradó kéréseket kezeli.

## 9.4. Metadata condition

A condition három része:

- mező;
- operátor;
- érték.

Példa:

```text
Field: metadata.plan
Operator: equals
Value: premium
```

A támogatott operátorok:

```text
equals
not_equals
contains
```

### Példák

Premium route:

```text
metadata.plan equals premium
```

Nem belső ügyfél:

```text
metadata.customer_type not_equals internal
```

Regional tag:

```text
metadata.regions contains eu
```

## 9.5. Timeout

A megengedett tartomány:

```text
1–120 másodperc
```

Ajánlás:

- interaktív chat: 10–30 másodperc;
- hosszabb reasoning: 30–90 másodperc;
- batch folyamat: külön, aszinkron workflow.

A teljes fallback-lánc worst-case ideje a target timeoutok és retryk összegéből áll. Ne állíts minden targetre túl magas timeoutot.

## 9.6. Retries

A megengedett tartomány:

```text
0–5
```

Ajánlás:

- 429 vagy átmeneti 5xx: 1 retry;
- timeout: legfeljebb 1 retry;
- determinisztikus 4xx: ne retryolj;
- költséges modell: inkább fallback, mint több retry.

## 9.7. Timezone és időablak

A target csak a megadott helyi időablakban jogosult.

Példa:

```text
Timezone: Europe/Zurich
Start: 08:00
End: 18:00
```

Éjszakán átívelő időablak is használható:

```text
Start: 18:00
End: 08:00
```

Ügyelj arra, hogy mindig maradjon legalább egy egész nap jogosult fallback.

## 9.8. Required capabilities

Elérhető követelmények:

- tools;
- structured output;
- streaming;
- embeddings.

Ha a kérés vagy route megköveteli a capabilityt, de a target nem támogatja, a target nem jogosult.

## 9.9. Fallback HTTP status codes

Itt adhatod meg, mely hibák váltsanak következő targetre.

Ajánlott alapérték:

```text
429, 500, 502, 503
```

Tipikus jelentésük:

- `429`: rate limit;
- `500`: provider belső hiba;
- `502`: hibás upstream gateway;
- `503`: átmeneti elérhetetlenség.

Ne állíts be automatikus fallbacket minden 4xx hibára. Egy hibás requestet másik provider sem feltétlenül tud teljesíteni.

## 9.10. Per-request cost ceiling

A target csak akkor jogosult, ha a kérés becsült költsége nem haladja meg a beállított plafont.

Példa:

```text
0.05 USD
```

A `0` érték azt jelenti, hogy nincs target-specifikus plafon.

---

## 10. Route-tervezési minták

## 10.1. Egyszerű primary + fallback

```text
Start
  ↓
Primary model
  ↓ on 429/5xx/timeout
Fallback model
  ↓
End
```

Ajánlott általános production route-hoz.

## 10.2. Költségtudatos route

```text
Start
  ↓
Olcsó primary model
  ↓ ha nem jogosult vagy sikertelen
Prémium fallback
  ↓
End
```

A drágább fallbackhez használj szigorú request-cost ceilingt.

## 10.3. Premium és free felhasználók

```text
Premium target
Condition: metadata.plan equals premium
  ↓ ha nem teljesül
Economy target
  ↓
End
```

## 10.4. Canary rollout

```text
New model
Mode: weighted
Weight: 10
  ↓ ha a sticky bucket nem jogosult
Current production model
  ↓
End
```

A canary targethez állíts be szigorú capability-, latency- és költségkorlátot.

## 10.5. Regionális route

```text
EU model
Condition: metadata.region equals eu
Timezone: Europe/Zurich
  ↓
Global fallback
```

Ha adatrezidencia kötelező, a fallbacknek is ugyanabban az engedélyezett régióban kell lennie.

## 10.6. Tool-calling route

Minden jogosult targetnél jelöld be:

```text
tools
```

Ha structured JSON is kell:

```text
tools
structured_output
```

Ne legyen olyan fallback, amely ezeket nem támogatja.

---

## 11. Mentés és draftkezelés

### 11.1. Save draft

Módosítás után a fejléc ezt mutatja:

```text
Unsaved changes
```

Válaszd a **Save draft** gombot.

A mentés:

- új immutable verziót hoz létre;
- nem módosítja az éles forgalmat;
- frissíti a draft verziószámot;
- új validációt igényel.

### 11.2. Miért kell új validáció?

A validáció mindig egy konkrét draft verzióhoz kötődik. Ha módosítod a route-ot, a korábbi validáció már nem bizonyítja az új konfiguráció helyességét.

---

## 12. Route validálása

Válaszd:

```text
Validate graph
```

A validátor ellenőrzi többek között:

- van-e legalább egy target;
- nincs-e duplikált modell;
- pozitívak-e a prioritások;
- támogatott-e a routing mode;
- 0–100 között van-e a weight;
- nem negatív-e a cost ceiling;
- megvan-e a condition field;
- 0–5 között van-e a retry;
- 1–120 másodperc között van-e a timeout;
- van-e fallback figyelmeztetés;
- terminál-e a folyamat.

### Hiba és figyelmeztetés közötti különbség

**Error:** blokkolja a publikálást.

**Warning:** nem feltétlenül blokkoló, de review során kezelendő.

Példa warning:

```text
route has no fallback target
```

---

## 13. Szimuláció használata

Nyisd meg a **Test** lapot, vagy válaszd a **Simulate** gombot.

A szimuláció kiértékeli:

1. a prioritást;
2. a capabilityket;
3. a budgetet;
4. a per-request költségplafont;
5. a metadata-feltételt;
6. a weighted sticky bucketet;
7. a fallback sorrendet.

Példa eredmény:

```json
{
  "selected_model": "@deepinfra-prod/deepseek-ai/DeepSeek-V3",
  "decision_path": [
    {
      "kind": "target",
      "model": "@premium/model",
      "eligible": false,
      "reason": "condition did not match"
    },
    {
      "kind": "target",
      "model": "@deepinfra-prod/deepseek-ai/DeepSeek-V3",
      "eligible": true,
      "reason": "eligible"
    }
  ],
  "provider_call_made": false
}
```

A szimuláció nem bizonyítja, hogy a provider ténylegesen működik. Ehhez használd a Provider Compatibility Labot, majd szükség esetén az explicit replay vagy test execution folyamatot.

---

## 14. Review & Publish

Válaszd a fejlécben:

```text
Review & publish
```

A rendszer validálja az aktuális draftot, majd megnyitja a review drawert.

A panel megmutatja:

- a draft verzióját;
- a jelenlegi live verziót;
- a targetek számát;
- a validation státuszt;
- a figyelmeztetéseket;
- a becsült napi költséget;
- a rollout módot.

### 14.1. Change reason

Írd le röviden, miért történik a változás.

Jó példa:

```text
A DeepInfra EU fallback hozzáadása a 429 hibák és az OpenAI EU rate limit csökkentésére.
```

Rossz példa:

```text
update
```

### 14.2. Rollout mód

Lehetőségek:

- közvetlen publikálás;
- 10%-os canary előkészítése.

Nagy hatású production route-nál elsőként canary megközelítést használj.

### 14.3. Publish validated draft

A gomb csak akkor aktív, ha az aktuális draft sikeresen validált.

Publikálás után:

- a draft verzió live lesz;
- a stabil route alias változatlan marad;
- az alkalmazások az új konfigurációt használják;
- a régi verzió megmarad rollback céljára.

---

## 15. Verziótörténet, összehasonlítás és rollback

Nyisd meg a **Versions** lapot.

Minden verziónál látható:

- verziószám;
- létrehozási idő;
- targetek száma;
- live vagy történeti állapot.

### 15.1. Compare

A szemantikus diff megmutatja:

- hozzáadott modellek;
- eltávolított modellek;
- megváltozott modellek.

A modell akkor is changed állapotú, ha például módosult:

- timeout;
- retry;
- condition;
- weight;
- cost ceiling;
- capability;
- schedule.

### 15.2. Restore as draft

A **Restore as draft** nem írja felül a történetet.

Ehelyett:

1. beolvassa a kiválasztott verzió konfigurációját;
2. új draft verziót készít;
3. változatlanul hagyja az előző verziókat;
4. új validációt kér;
5. csak ezután enged publikálást.

Ez a biztonságos rollback útja.

---

## 16. Route duplikálása

A route menüjében válaszd a **Duplicate** műveletet.

A másolat:

- új route ID-t kap;
- új nevet kap;
- külön draftként kezelhető;
- nem befolyásolja az eredeti live route-ot.

Használd:

- új ügyfél vagy régió route-jának kiindulópontjához;
- kísérleti konfigurációhoz;
- nagy átalakítás előtt;
- sablonkészítéshez.

---

## 17. Archiválás

A route menüjében válaszd az **Archive** műveletet.

A gateway először dependency checket futtat.

Lehetséges blokkoló függőség:

- alkalmazás default route-ja;
- más route hivatkozása;
- aktív konfigurációs kapcsolat.

Ha alkalmazás használja a route-ot, először rendelj hozzá más default route-ot.

Sikeres archiválás után:

- a route kikerül az aktív listából;
- új forgalmat nem fogad;
- verziói megmaradnak;
- visszaállítható;
- auditadatai megmaradnak.

---

## 18. Archivált route visszaállítása

1. Kapcsold be az **Archived routes** nézetet.
2. Nyisd meg a route `⋯` menüjét.
3. Válaszd a **Restore** műveletet.

A route draft állapotban tér vissza. Nem lesz automatikusan live.

Ezután:

1. ellenőrizd a provider kapcsolatokat;
2. szinkronizáld a modelleket;
3. futtasd a validációt;
4. futtasd a szimulációt;
5. publikáld a route-ot.

---

## 19. Végleges törlés

Végleges törlés csak akkor engedélyezett, ha:

- a route archivált;
- nincs blokkoló függősége;
- a felhasználó pontosan beírja a route nevét.

A megerősítő mezőben például ezt kell megadni:

```text
support-global
```

A **Delete permanently** gomb addig inaktív, amíg a név nem egyezik.

A végleges törlés:

- eltávolítja a route-ot;
- eltávolítja a route verzióit;
- nem állítható vissza.

Production route esetén előnyben részesítsd az archiválást. Végleges törlést csak retention és auditkövetelmények ellenőrzése után végezz.

---

## 20. Ajánlott route-tervezési szabályok

### 20.1. Legyen legalább egy fallback

Egyetlen target esetén a route egyszerű, de nem ellenálló.

### 20.2. A fallback legyen valóban független

Lehetőleg:

- más provider connection;
- más régió;
- eltérő rate-limit tartomány;
- kompatibilis modellképesség.

Három modell ugyanazon provider ugyanazon fiókjában nem jelent teljes szolgáltatói redundanciát.

### 20.3. Ne növeld korlátlanul a retryt

A sok retry:

- növeli a latencyt;
- növeli a költséget;
- tovább terheli a hibás providert;
- késlelteti a valódi fallbacket.

### 20.4. A fallback capabilityben ne legyen gyengébb

Ha a route tool callingot igényel, minden fallback tudjon tool callingot.

### 20.5. Figyeld a worst-case latencyt

Példa:

```text
Primary: 20 s timeout + 1 retry
Fallback 1: 20 s timeout + 1 retry
Fallback 2: 30 s timeout
```

A legrosszabb eset könnyen meghaladhatja a kliens teljes timeoutját.

### 20.6. A költséget teljes láncra tervezd

Egy request több modellt is meghívhat, ha retry vagy fallback történik.

### 20.7. Kondíció után mindig legyen default ág

Ha egy premium condition nem teljesül, legyen economy vagy általános fallback.

### 20.8. Weighted canary után legyen stabil baseline

A canary target kimaradó bucketjeinek mindig egy megbízható production modellre kell kerülniük.

### 20.9. Változtatás előtt szimulálj

Legalább ezeket teszteld:

- premium metadata;
- free metadata;
- nulla budget;
- magas request cost;
- hiányzó tools capability;
- canary bucketbe eső kérés;
- canary bucketből kimaradó kérés.

### 20.10. Production változáshoz írj change reasont

A change reason legyen elegendő ahhoz, hogy egy későbbi incidensnél megérthető legyen a módosítás célja.

---

## 21. Gyakori hibák és megoldások

### Nem jelenik meg modell a választóban

**Ok:** a provider model catalog nincs szinkronizálva.

**Megoldás:** Providers → Test & sync models.

### A route nem publikálható

**Okok:**

- mentetlen módosítás;
- sikertelen validáció;
- a validáció nem az aktuális draft verzióra vonatkozik;
- hibás weight;
- negatív költségplafon;
- hibás retry vagy timeout;
- hiányzó condition field.

**Megoldás:** mentsd a draftot, futtasd újra a validációt, és javítsd a jelzett hibákat.

### A szimuláció szerint nincs jogosult target

**Lehetséges okok:**

- minden condition hamis;
- nulla a fennmaradó budget;
- a request cost meghaladja minden target plafonját;
- hiányzik egy szükséges capability;
- minden weighted target kizárja a sticky bucketet.

**Megoldás:** adj hozzá megfelelő default fallbacket.

### A route nem archiválható

**Ok:** alkalmazás használja default route-ként.

**Megoldás:** rendelj az alkalmazáshoz másik route-ot, majd próbáld újra.

### Nem aktív a Delete permanently gomb

**Ok:**

- a route nem archivált; vagy
- a megerősítő név nem pontos.

**Megoldás:** archiváld a route-ot, majd írd be pontosan a teljes route-nevet.

### A restored route nem live

Ez szándékos. A restore új draftot hoz létre. Validáld és publikáld külön.

---

## 22. Ajánlott üzemeltetési folyamat

### Development

1. Provider felvétele.
2. Model sync.
3. Route létrehozása.
4. Primary és fallback targetek beállítása.
5. Condition, weight és budget beállítása.
6. Save draft.
7. Validate graph.
8. Simulation több esettel.

### Staging

1. Compatibility Lab futtatása.
2. Explicit test vagy replay.
3. Latency és költség ellenőrzése.
4. Hibás provider vagy 429 szimuláció.
5. Version diff ellenőrzése.

### Production

1. Review & Publish megnyitása.
2. Warningok áttekintése.
3. Change reason kitöltése.
4. Canary vagy azonnali rollout kiválasztása.
5. Publikálás.
6. Activity és Usage figyelése.
7. Incidens esetén Version diff.
8. Korábbi verzió Restore as draft.
9. Validáció és újrapublikálás.

---

## 23. Gyors ellenőrzőlista publikálás előtt

- [ ] A provider kapcsolatok egészségesek.
- [ ] A modellek frissen szinkronizáltak.
- [ ] A compatibility check friss.
- [ ] Van elsődleges target.
- [ ] Van legalább egy valódi fallback.
- [ ] A fallback tudja a szükséges capabilityket.
- [ ] A retry legfeljebb a szükséges érték.
- [ ] A timeout belefér a kliens teljes időkeretébe.
- [ ] A fallback HTTP-kódok helyesek.
- [ ] A metadata conditionnek van default útvonala.
- [ ] A weighted route-nak van baseline targetje.
- [ ] A költségplafonok megfelelőek.
- [ ] A regionális elvárások minden ágon teljesülnek.
- [ ] A simulation legalább öt reprezentatív esettel lefutott.
- [ ] A validáció az aktuális draft verzióra sikeres.
- [ ] A version diff érthető és várt.
- [ ] A change reason kitöltött.
- [ ] Rollbackhez rendelkezésre áll egy korábbi működő verzió.

---

## 24. Rövid példa: production support route

### Cél

- Premium ügyfeleknek nagyobb minőségű modell.
- Free ügyfeleknek olcsóbb modell.
- Providerhiba esetén másik szolgáltató.
- Tool calling minden ágon.
- Költséglimit kérésenként.

### Target 1: premium

```text
Model: @openai-prod/gpt-4.1
Mode: conditional
Condition: metadata.plan equals premium
Timeout: 20
Retries: 1
Required capabilities: tools, structured_output
Fallback HTTP codes: 429, 500, 502, 503
Cost ceiling: 0.08 USD
```

### Target 2: economy

```text
Model: @deepinfra-prod/deepseek-ai/DeepSeek-V3
Mode: fallback
Timeout: 18
Retries: 1
Required capabilities: tools, structured_output
Fallback HTTP codes: 429, 500, 502, 503
Cost ceiling: 0.03 USD
```

### Target 3: independent provider fallback

```text
Model: @xiaomi-mimo-prod/mimo-v2.5-pro
Mode: fallback
Timeout: 25
Retries: 0
Required capabilities: tools, structured_output
Cost ceiling: 0.05 USD
```

### Tesztesetek

```text
1. plan=premium, budget=1 USD, cost=0.02 → premium modell
2. plan=free, budget=1 USD, cost=0.02 → economy modell
3. plan=free, tools hiányzik → nincs jogosult tools target
4. plan=premium, cost=0.10 → premium kiesik költség miatt
5. economy provider 429 → Xiaomi fallback fut éles végrehajtáskor
```

---

## 25. Összefoglalás

A Gateway Router célja, hogy az AI-modellválasztás ne legyen az alkalmazáskódba égetve. A Route Studio segítségével a route-ok:

- vizuálisan tervezhetők;
- verziózottak;
- validálhatók;
- providerhívás nélkül szimulálhatók;
- szemantikusan összehasonlíthatók;
- biztonságosan publikálhatók;
- visszaállíthatók;
- archiválhatók;
- kontrolláltan törölhetők.

A legfontosabb működési szabály:

> **Ne publikálj route-ot pusztán azért, mert elmenthető. Publikálj csak akkor, ha az aktuális draft validált, reprezentatív esetekkel szimulált, a fallbackek kompatibilisek, a költség és latency elfogadható, és rendelkezésre áll egy biztonságos rollback verzió.**
