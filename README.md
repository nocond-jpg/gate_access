# Gate Access

Integracja Home Assistant do zarządzania dostępem do bramy/furtki przez
**osobne linki (webhooki) dla każdej osoby**, z historią, statystykami i panelem.
_A Home Assistant integration for per-person gate access links with history,
statistics and an admin panel._

> Zastępuje kruche rozwiązania oparte na `!include` + `shell_command` jedną
> integracją: webhooki rejestrowane w locie, link zwraca stronę „Brama otwarta",
> a wszystkim zarządzasz z panelu w menu bocznym.

## Funkcje

- **Link per osoba** — każdy dostaje własny, losowy URL otwierający bramę.
  Otwarty w przeglądarce pokazuje stronę „Brama otwarta" (bez pobierania pliku).
- **Typy dostępu** — stały, czasowy (okno **od–do**, domyślnie od teraz),
  na liczbę użyć, jednorazowy.
- **Włącz/wyłącz** dostęp bez kasowania; **reset** licznika użyć.
- **Wiele obiektów** — brama, furtka i inne: `cover`, `lock`, `switch`,
  `input_boolean`, `button`, `input_button`, `script`, `light`.
- **Auto-zamykanie** po zadanym czasie (opcjonalne, per typ obiektu).
- **Szybkie otwarcie** z panelu (bez linku), przypisane do użytkownika.
- **Historia** otwarć/zamknięć ze źródłem i osobą; filtry po obiekcie i użytkowniku.
- **Statystyki** otwarć per obiekt (dziś / miesiąc / rok / razem) — opcjonalne.
- **Rate limit** (domyślnie 2/min na link).
- **Kasowanie historii** (pojedynczo i całość) z opcjonalnym hasłem.
- **Karta Lovelace** do tworzenia linków oraz **usługa** `gate_access.log_open`
  do zapisu otwarć z automatyzacji (np. NFC na wideodomofonie).
- **Udostępnianie panelu** wszystkim użytkownikom HA (opcja).
- **Aplikacja na telefon (PWA)** z przyciskami Otwórz/Zamknij, stanem bram
  i **przypomnieniem push**, gdy brama zostanie otwarta (od 1.7).

## Instalacja

### HACS (repozytorium własne)
1. HACS → Integracje → menu ⋮ → **Custom repositories**.
2. Dodaj `https://github.com/nocond-jpg/gate-access`, kategoria **Integration**.
3. Zainstaluj „Gate Access", zrestartuj Home Assistant.

### Ręcznie
1. Skopiuj folder `custom_components/gate_access` do `config/custom_components/`.
2. Zrestartuj Home Assistant.

## Konfiguracja

Ustawienia → Urządzenia i usługi → **Dodaj integrację** → „Gate Access".
Wskaż obiekty (brama/furtka…), plik logu i (opcjonalnie) czas auto-zamykania.

Dalsze ustawienia w **Konfiguruj** (menu):
- **Obiekty, log i auto-zamykanie**
- **Rejestr i statystyki** — statystyki, rejestr zamknięć, limit otwarć/min,
  hasło do kasowania historii
- **Udostępnianie panelu** — widoczność dla wszystkich użytkowników HA


## Aplikacja na telefon (od 1.7)

Każda osoba może mieć Bramę jako ikonę na ekranie telefonu — bez App Store,
konta Apple Developer, konta HA i aplikacji Home Assistant.

1. Panel → **Dostępy** → przy osobie **Aplikacja** (kopiuje `…/api/webhook/<id>?app=1`).
   Wyślij SMS-em/Signalem.
2. iPhone: otwórz link w **Safari** → **Udostępnij** → **Do ekranu początkowego**.
3. Otwórz Bramę z ikony → **Włącz przypomnienia** → zezwól (iOS 16.4+).

W szczegółach dostępu („Aplikacja na telefon”) zaznacz obiekty, które osoba widzi
(np. trzy bramy i furtka). Strona aplikacji niczego nie otwiera sama — tylko dotknięcie.

### Stan i przypomnienia
**Konfiguruj → Stan bram i przypomnienia**: dla każdego obiektu wskaż czujnik stanu
(np. kontaktron `binary_sensor`, włączony = otwarta; puste = stan samego obiektu,
np. `cover`) i po ilu minutach wysłać przypomnienie (0 = wyłączone).
Przypomnienie dostaje **osoba, która otworzyła** — do 3 razy co N minut,
dopóki czujnik pokazuje otwarcie. Dotknięcie powiadomienia otwiera aplikację
z zaznaczoną bramą i przyciskiem **Zamknij**.
Obiekty typu `button`/`script` bez czujnika nie mają stanu, więc nie mają przypomnień.
Zamykanie z aplikacji: `cover`, `lock`, `switch`, `input_boolean`, `light`.

### Siri / CarPlay (Skróty Apple)
Skrót „Gate”: akcja **Pobierz zawartość URL**
`https://<adres>/api/webhook/<id>?do=open&t=<entity_id>`, metoda **POST**.
Odpowiedź JSON `{"ok": true}`. W aucie: „Hey Siri, Gate”.
Automatyzacja **Przyjazd** → *Uruchom po potwierdzeniu* daje powiadomienie
z pytaniem przy zbliżaniu się do bramy.

### Bezpieczeństwo
Link jest kluczem — kto go ma, otwiera (w ramach okna czasowego, limitu użyć
i limitu prób). Wyłączenie lub usunięcie dostępu w panelu działa natychmiast,
także dla aplikacji i przypomnień.

## Użycie

W menu bocznym pojawia się panel **Brama – dostęp**:
- **Panel** — utwórz dostęp (imię + typ + obiekt) i „Szybkie otwarcie".
- **Dostępy** — lista z filtrami; rozwiń wiersz, by zmienić ustawienia i zobaczyć
  historię danego linku; akcje: kopiuj, otwórz, włącz/wyłącz, reset, usuń.
- **Historia** — wszystkie otwarcia/zamknięcia + statystyki; filtry i kasowanie.
- **Ustawienia** — podsumowanie i skrót do opcji integracji.

Karta Lovelace: `type: custom:gate-access-card`.

## Zapis otwarć z automatyzacji (NFC / wideodomofon)

```yaml
action: gate_access.log_open
data:
  name: Jan            # osoba (np. przypisana do karty NFC)
  source: NFC domofon
  target: cover.brama  # opcjonalnie
```

## Bezpieczeństwo

- `webhook_id` jest losowy (96 bitów) i pełni rolę hasła — traktuj link jak klucz.
- Dostęp z zewnątrz wymaga publicznej dostępności HA (własna domena / reverse
  proxy / Cloudflare Tunnel). Webhooki mają `local_only: false`.
- Panel domyślnie tylko dla administratorów; API panelu wymaga zalogowania w HA.
- Rate limit ogranicza nadużycia linków.

## Wymagania

Home Assistant **2024.7** lub nowszy.

## Licencja

[MIT](LICENSE).
