# Changelog

Wszystkie istotne zmiany w tym projekcie są dokumentowane w tym pliku.
Format oparty na [Keep a Changelog](https://keepachangelog.com/),
wersjonowanie wg [SemVer](https://semver.org/).

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
