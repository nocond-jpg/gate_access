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

### Otwieranie jednym dotknięciem (od 1.7.1)
- **Ikona na ekran**: w aplikacji przy obiekcie dotknij „Ikona na ekran – otwieranie
  jednym dotknięciem” → link zostaje skopiowany → otwórz go w Safari/Chrome →
  *Do ekranu początkowego*. Dotknięcie tej ikony od razu otwiera obiekt.
- **Android – skróty aplikacji**: przytrzymaj ikonę Brama → „Otwórz: …”; skrót można
  przeciągnąć na ekran główny.

### Stan i przypomnienia
**Konfiguruj → Stan bram i przypomnienia**: dla każdego obiektu wskaż czujnik stanu
(np. kontaktron `binary_sensor`, włączony = otwarta; puste = stan samego obiektu,
np. `cover`) i po ilu minutach wysłać przypomnienie (0 = wyłączone).
Przypomnienie dostaje **osoba, która otworzyła** — do 3 razy co N minut,
dopóki czujnik pokazuje otwarcie. Dotknięcie powiadomienia otwiera aplikację
z zaznaczoną bramą i przyciskiem **Zamknij**.
Obiekty typu `button`/`script` bez czujnika nie mają stanu, więc nie mają przypomnień.
Zamykanie z aplikacji: `cover`, `lock`, `switch`, `input_boolean`, `light`.

### Zatwierdzanie urządzeń (od 1.8)
Aplikacja podpisuje każde otwarcie kluczem zapisanym w telefonie (nie da się go
skopiować). Pierwsze uruchomienie wysyła prośbę o zatwierdzenie:
**Konfiguruj → Urządzenia aplikacji (zatwierdzanie)** → np. `notify.mobile_app_samsung`
— przyjdzie powiadomienie z przyciskami **Zatwierdź / Odrzuć**. Urządzenia każdej
osoby widać w panelu (Dostępy → osoba → Aplikacja na telefon) z przyciskami
Zatwierdź / Usuń. Na iPhonie każda ikona na ekranie ma własny klucz i wymaga
jednorazowego zatwierdzenia.

### Siri / CarPlay (Skróty Apple)
Skrót „Gate”: akcja **Pobierz zawartość URL**
`https://<adres>/api/webhook/<id>?do=open`, metoda **POST** (pojedynczy link —
otwiera obiekt przypisany do linku; bez podpisu).
Odpowiedź JSON `{"ok": true}`. W aucie: „Hey Siri, Gate”.
Automatyzacja **Przyjazd** → *Uruchom po potwierdzeniu* daje powiadomienie
z pytaniem przy zbliżaniu się do bramy.

### Bezpieczeństwo
Link jest kluczem — kto go ma, otwiera (w ramach okna czasowego, limitu użyć
i limitu prób). Wyłączenie lub usunięcie dostępu w panelu działa natychmiast,
także dla aplikacji i przypomnień.

## Samochód (Tesla) – od 1.9

1. Na telefonie w aplikacji Brama dotknij **Połącz z samochodem (Tesla)** – pojawi się
   adres i jednorazowy kod (5 min).
2. W aucie otwórz przeglądarkę, wpisz adres `twoja-domena/api/webhook/tesla`, na
   klawiaturze ekranowej wpisz kod i **Połącz**.
3. Strona przejdzie do widoku samochodowego z dużymi przyciskami – dodaj ją do ulubionych.
   Samochód jest zapisany jako urządzenie „Tesla · przeglądarka” (do usunięcia w panelu).

Adres parowania działa tylko przez 5 minut od wygenerowania kodu (do jego użycia).
Otwieranie zawsze wymaga dotknięcia przycisku; strona nie otwiera bramy sama.

## Bluetooth – awaryjne otwieranie bez sieci (od 2.0, Android)

Gdy telefon nie ma zasięgu sieci domowej albo Home Assistant nie działa, aplikacja Brama
(Chrome na Androidzie) steruje bramą bezpośrednio przez Bluetooth LE ze sterownikiem ESPHome.

**Sterownik:** komponent `brama_ble` z gałęzi `esphome` tego repozytorium
(`external_components: github://nocond-jpg/gate_access@esphome`) i usługa GATT
`d38d0001-7e51-45b5-9f87-ae96680dcaf6` – przykład w `esphome/brama.yaml`.

**Konfiguracja:** Ustawienia → Gate Access → **Bluetooth – awaryjne otwieranie** → przy
obiekcie wybierz akcję ESPHome (np. `brama_ble_klucze`). Integracja sama wysyła klucze
po każdej zmianie (osoby, urządzenia, wygaśnięcie – sprawdzane co 5 min) i co 6 h;
ręcznie: usługa `gate_access.sync_ble_keys`.

**Użycie:** aplikację trzeba raz otworzyć przy działającym domu (zapisuje się wtedy do
pracy bez sieci). Bez połączenia z domem pojawia się przycisk **Otwórz przez Bluetooth**;
Chrome pokaże listę urządzeń – wybierz bramę. Przy działającym domu przycisk Bluetooth
też jest dostępny.

**Zabezpieczenia:**
- ESP zna tylko klucze **publiczne** zatwierdzonych telefonów – z pamięci sterownika nie
  da się wydobyć niczego, co otwiera bramę. Klucz prywatny jest nieeksportowalny (telefon).
- Każde polecenie jest podpisane (ECDSA P-256) na jednorazowym, losowym nonce ze
  sterownika, ważnym 30 s – nagranie i odtworzenie transmisji nic nie da.
- Klucze dostają tylko zatwierdzone urządzenia osób włączonych, z dostępem stałym
  lub czasowym, do tego obiektu. Linki z limitem użyć nie działają przez Bluetooth
  (licznika nie da się pilnować bez HA).
- Dostęp czasowy jest sprawdzany na sterowniku; gdy sterownik nie zna godziny
  (restart bez HA i internetu), dostęp czasowy przez Bluetooth jest odrzucany.
- 5 błędnych prób w 10 min → blokada Bluetooth na 10 min. Przełącznik
  „Bluetooth – otwieranie” w ESPHome wyłącza tę drogę całkowicie.
- Usunięcie osoby/urządzenia zabiera klucz przy najbliższej synchronizacji – jeśli
  sterownik jest wtedy niedostępny, do czasu ponownego połączenia z HA stary klucz działa.

iPhone (Safari) nie obsługuje Web Bluetooth – tam działa tylko sterowanie przez sieć.

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
