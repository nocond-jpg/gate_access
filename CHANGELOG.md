# Changelog

Wszystkie istotne zmiany w tym projekcie są dokumentowane w tym pliku.
Format oparty na [Keep a Changelog](https://keepachangelog.com/),
wersjonowanie wg [SemVer](https://semver.org/).

## [2.0.2] - 2026-11-09

### Dodane
- Otwarcia/zamknięcia przez Bluetooth trafiają do historii i logu (źródło „Bluetooth”, osoba
  wg urządzenia). ESP wysyła zdarzenie `esphome.gate_access_ble`; gdy HA nie działa, sterownik
  trzyma do 20 zdarzeń i wysyła je po połączeniu – z czasem otwarcia.
  Wymaga w integracji ESPHome dla sterownika opcji „Allow the device to perform Home Assistant actions”.

## [2.0.1] - 2026-11-09

### Poprawione
- Bluetooth: wyszukiwanie bramy także po nazwie („brama”), nie tylko po UUID usługi – część
  telefonów nie pokazywała sterownika na liście Chrome. Czytelny komunikat, gdy Chrome nie ma
  uprawnień do Bluetooth.

## [2.0.0] - 2026-11-09

### Dodane
- **Bluetooth – awaryjne otwieranie bez sieci (Android, Chrome).** Aplikacja łączy się
  przez BLE ze sterownikiem ESPHome (komponent `brama_ble`, gałąź `esphome`) i wysyła
  polecenie podpisane kluczem urządzenia na jednorazowym nonce ze sterownika.
- Synchronizacja kluczy publicznych zatwierdzonych urządzeń do ESPHome (opcje →
  Bluetooth; po każdej zmianie, co 5 min sprawdzanie, co 6 h ponowne wysłanie) i usługa
  `gate_access.sync_ble_keys`.
- Aplikacja uruchamia się bez sieci / przy niedziałającym HA (service worker trzyma
  stronę i ikony); wtedy dostępne jest sterowanie przez Bluetooth.

### Zmienione
- Service worker rejestrowany zawsze (nie tylko przy włączaniu powiadomień).

## [1.9.1] - 2026-11-03

### Zmienione
- Adres parowania `/api/webhook/tesla` jest rejestrowany dopiero po wygenerowaniu kodu
  na telefonie i usuwany po użyciu kodu albo po jego wygaśnięciu (5 min). Poza parowaniem
  adres nie istnieje.

## [1.9.0] - 2026-11-03

### Dodane
- **Tryb samochodowy (Tesla).** Przeglądarka w aucie nie pozwala wkleić linku, więc
  samochód łączy się kodem: w aplikacji na telefonie „Połącz z samochodem (Tesla)” daje
  jednorazowy 6-cyfrowy kod (ważny 5 min, podpisane żądanie z zatwierdzonego urządzenia),
  w aucie wpisuje się krótki adres `…/api/webhook/tesla` i kod na klawiaturze ekranowej.
  Samochód dostaje własny klucz urządzenia, zatwierdzony automatycznie (kod wydało już
  zatwierdzone urządzenie tej osoby). Blokada 10 min po 10 błędnych kodach.
- Widok samochodowy (`?car=1` lub przeglądarka Tesli): duże przyciski w siatce, bez
  ikon na ekran i powiadomień push.
- Historia: zatwierdzenie urządzenia ze źródłem „kod samochodu”.

## [1.8.0] - 2026-11-02

### Dodane
- **Podpisywanie w aplikacji (powiązanie z urządzeniem).** Przy pierwszym uruchomieniu
  aplikacja tworzy w telefonie klucz P-256 (WebCrypto, nieeksportowalny, IndexedDB)
  i rejestruje klucz publiczny w HA. Każde otwarcie/zamknięcie z aplikacji jest
  podpisane i oparte o jednorazowe wyzwanie (30 s) — link skopiowany na inny telefon
  nie otworzy niczego z poziomu aplikacji.
- **Zatwierdzanie nowych urządzeń przez administratora**: powiadomienie z przyciskami
  Zatwierdź/Odrzuć na telefon z aplikacją Home Assistant (menu „Urządzenia aplikacji
  (zatwierdzanie)”), powiadomienie trwałe w HA oraz lista urządzeń w panelu
  (Zatwierdź / Odrzuć / Usuń).
- Historia: zdarzenia „urządzenie zatwierdzone / usunięte”, filtr „Urządzenia”.

### Zmienione
- Akcje aplikacji (`?do=…&t=…`), stan bram i subskrypcje push wymagają zatwierdzonego
  urządzenia. **Pojedyncze linki działają bez zmian** (strona otwarcia, `?do=open`,
  `?do=close` bez `t`) — także dla Skrótów Apple / HTTP Shortcuts.
- Usunięcie urządzenia usuwa też jego subskrypcje przypomnień.

## [1.7.2] - 2026-11-02

### Poprawione
- Nieaktualny skrót aplikacji (po odebraniu dostępu do obiektu) pokazuje komunikat
  „Ten skrót jest nieaktualny” zamiast pustego ekranu; nieaktualna ikona jednego
  dotknięcia pokazuje „Ikona nieaktualna”. Serwer i tak odmawia otwarcia.

## [1.7.1] - 2026-11-02

### Dodane
- **Skróty aplikacji (Android)** — przytrzymanie ikony Brama pokazuje „Otwórz: <obiekt>”
  (do 4 obiektów); skrót można przeciągnąć na ekran jako osobną ikonę.
- **Ikona na ekran – otwieranie jednym dotknięciem** (iPhone i Android) — przycisk
  przy każdym obiekcie w aplikacji; osobna ikona z nazwą obiektu, której dotknięcie
  od razu otwiera ten obiekt. Ikona garażu dla obiektów `device_class: garage`.
- Otwarcie przy starcie działa wyłącznie po uruchomieniu z ikony/skrótu (tryb aplikacji).
  Ten sam link otwarty w przeglądarce nie otwiera niczego.

## [1.7.0] - 2026-11-02

### Dodane
- **Aplikacja na telefon (PWA)** pod linkiem osoby: `…/api/webhook/<id>?app=1`.
  Dodawana do ekranu początkowego iPhone/Android, bez App Store, bez konta HA
  i bez aplikacji Home Assistant. Lista obiektów osoby ze stanem (otwarta/zamknięta),
  przyciski **Otwórz** i **Zamknij**. Wejście na stronę niczego nie otwiera —
  działa wyłącznie ręczne dotknięcie.
- **Wiele obiektów w aplikacji** — w szczegółach dostępu sekcja „Aplikacja na telefon”
  z wyborem obiektów (np. 3 bramy + furtka). Zwykły link działa jak dotąd (jeden obiekt).
- **Przypomnienia push „nadal otwarta”** (Web Push, VAPID — bez konta Apple Developer)
  wysyłane do osoby, która otworzyła, gdy obiekt jest otwarty dłużej niż N minut
  (domyślnie 10, do 3 powtórzeń). Kasowane od razu, gdy czujnik pokaże zamknięcie.
- Menu **„Stan bram i przypomnienia”**: czujnik stanu per obiekt (np. kontaktron
  `binary_sensor`) i czas przypomnienia per obiekt.
- Panel: przycisk **Aplikacja** (kopiuje link aplikacji), lista urządzeń z przypomnieniami,
  **Wyślij test**, **Usuń urządzenia**; w Ustawieniach stan i przypomnienia każdego obiektu.
- Źródło „aplikacja” w historii.

### Bezpieczeństwo
- Subskrypcje push przyjmowane tylko dla znanych usług (Apple/Google/Mozilla/Microsoft) —
  link nie pozwala skierować żądań HA na dowolny adres.
- Maks. 5 urządzeń na osobę; martwe subskrypcje usuwane automatycznie.
- Aplikacja może sterować tylko obiektami przypisanymi osobie; wyłączenie/wygaśnięcie
  dostępu blokuje także aplikację i przypomnienia.

### Wymagania
- Nowa zależność `pywebpush==1.14.1` (ta sama wersja co w integracji HTML5 w HA).

## [1.6.0] - 2026-07-08

### Dodane
- **Favicon** na stronach otwarcia/potwierdzenia linku — ikona dobierana automatycznie:
  brama garażowa dla obiektów `device_class: garage`, w innym wypadku zwykła brama.

## [1.5.0] - 2026-07-08

### Dodane
- Tryb **„Wymagaj dotknięcia, aby otworzyć"** (menu „Auto-zamykanie i przycisk").
  Wejście na link tylko pokazuje stronę z przyciskiem „Otwórz bramę"; brama otwiera
  się dopiero po dotknięciu. Rozwiązuje otwieranie przez automatyczne odświeżanie
  strony i podglądy linków na iPhone (Safari/Wiadomości/Mail).

## [1.4.0] - 2026-07-08

### Dodane
- **Zamykanie z panelu** — w sekcji „Szybkie sterowanie" każda brama ma przyciski
  „Otwórz" i „Zamknij" (zamykanie dla obiektów, które je wspierają). Zapis jako
  źródło „panel", przypisane do zalogowanego użytkownika.

## [1.3.0] - 2026-07-08

### Dodane
- Opcja **stałego adresu bazowego** dla linków webhook (menu „Obiekty i log").
  Gdy HA działa pod kilkoma adresami, linki są zawsze budowane z wybranego adresu,
  a nie z tego, na którym akurat otwarto panel. Puste = bieżący adres przeglądarki.

## [1.2.1] - 2026-07-08

### Poprawione
- Przycisk „Zamknij bramę" na stronie otwarcia nie wygasza się już na stałe —
  po kliknięciu jest chwilowo blokowany (na czas żądania), po czym znów działa,
  więc można ponowić zamknięcie. Odliczanie nie blokuje przycisku.

## [1.2.0] - 2026-07-08

### Dodane
- Otwarcia z **pilota/lokalne** (bez użytkownika HA) są zapisywane w historii jako
  „Pilot" i **liczone w statystykach**.
- **Auto-zamykanie osobno dla każdej bramy** (menu „Auto-zamykanie i przycisk"),
  działające także przy otwarciu z pilota i automatu.
- Opcjonalny **przycisk „Zamknij bramę"** na stronie po otwarciu linku.
- **Odliczanie** do auto-zamknięcia na przycisku zamknięcia (gdy auto-zamykanie aktywne).

### Zmienione
- Auto-zamykanie planowane centralnie przy każdym otwarciu (spójne dla wszystkich źródeł).

## [1.1.0] - 2026-07-08

### Dodane
- Przycisk **Reaktywuj** na wygasłych/wyłączonych dostępach — jednym kliknięciem
  włącza i re-arm'uje link (licznik do poprzedniej wartości; czasowy przedłużony o 24 h).
- Nieudane próby w **Historii**: użycie wygasłego/wyłączonego/nieaktywnego linku
  oraz przekroczenie limitu (zapisywane, z ograniczeniem do 1/min na link).
- Filtr **Zdarzenie** w Historii: wszystkie / otwarcia / nieudane próby / zamknięcia.

## [1.0.0] - 2026-07-08

Pierwsze publiczne wydanie.

### Dodane
- Tworzenie osobnego webhooka (linku) otwierającego bramę dla każdej osoby,
  rejestrowanego w locie (bez edycji YAML i bez przeładowania automatyzacji).
- Handler linku zwraca stronę HTML „Brama otwarta" — link można otworzyć w
  przeglądarce, bez pobierania pliku.
- Typy dostępu: stały, czasowy (okno **od–do**, domyślnie od teraz),
  na liczbę użyć, jednorazowy.
- Ręczne **włączanie/wyłączanie** dostępu bez kasowania.
- **Reset** licznika użyć (ten sam link znów działa).
- Obsługa **wielu obiektów** (brama, furtka, …): `cover`, `lock`, `switch`,
  `input_boolean`, `button`, `input_button`, `script`, `light` — akcja
  otwarcia/zamknięcia dobierana wg typu encji.
- Opcjonalne **auto-zamykanie** po zadanym czasie (per typ obiektu).
- **Szybkie otwarcie** z panelu (bez linku), przypisane do zalogowanego
  użytkownika.
- **Historia** wszystkich otwarć/zamknięć ze źródłem (link / panel / HA /
  usługa) i osobą; filtrowanie po obiekcie i użytkowniku.
- Opcjonalne **statystyki** otwarć per obiekt: dziś / miesiąc / rok / razem.
- Opcjonalne rejestrowanie **zamknięć**.
- Usługa `gate_access.log_open` do zapisu otwarć z automatyzacji
  (np. NFC na wideodomofonie).
- **Rate limit** otwarć na link (domyślnie 2/min, konfigurowalny).
- Kasowanie historii (pojedyncze wpisy i całość) z opcjonalnym hasłem.
- Panel boczny z zakładkami (Panel / Dostępy / Historia / Ustawienia) oraz
  karta Lovelace do tworzenia linków.
- Udostępnianie panelu wszystkim użytkownikom HA (opcja) — nie tylko adminom.
- Eksport dostępów do CSV / JSON.
- Menu ustawień w opcjach integracji.

[1.6.0]: https://github.com/nocond-jpg/gate-access/releases/tag/v1.6.0
[1.5.0]: https://github.com/nocond-jpg/gate-access/releases/tag/v1.5.0
[1.4.0]: https://github.com/nocond-jpg/gate-access/releases/tag/v1.4.0
[1.3.0]: https://github.com/nocond-jpg/gate-access/releases/tag/v1.3.0
[1.2.1]: https://github.com/nocond-jpg/gate-access/releases/tag/v1.2.1
[1.2.0]: https://github.com/nocond-jpg/gate-access/releases/tag/v1.2.0
[1.1.0]: https://github.com/nocond-jpg/gate-access/releases/tag/v1.1.0
[1.0.0]: https://github.com/nocond-jpg/gate-access/releases/tag/v1.0.0
