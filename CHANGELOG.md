# Changelog

Wszystkie istotne zmiany w tym projekcie są dokumentowane w tym pliku.
Format oparty na [Keep a Changelog](https://keepachangelog.com/),
wersjonowanie wg [SemVer](https://semver.org/).

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

[1.0.0]: https://github.com/nocond-jpg/gate-access/releases/tag/v1.0.0
