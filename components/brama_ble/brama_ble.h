// brama_ble.h – awaryjne otwieranie bramy przez Bluetooth LE (Gate Access)
//
// Zasada działania:
//  * Home Assistant (integracja Gate Access) wgrywa przez akcję API `ble_klucze` listę kluczy
//    PUBLICZNYCH zatwierdzonych telefonów (P-256). Klucze prywatne nigdy nie opuszczają telefonu
//    (WebCrypto, klucz nieeksportowalny) – z pamięci ESP nie da się wyciągnąć niczego, co otwiera bramę.
//  * Po połączeniu telefon czyta jednorazowy nonce (16 B z esp_fill_random), podpisuje
//    "ble1|<urządzenie>|<nonce hex>|<akcja>" i zapisuje polecenie w kawałkach po 18 B.
//  * ESP sprawdza: nonce (jednorazowy, ważny 30 s), znane urządzenie, okno ważności dostępu
//    (dostęp czasowy wymaga ustawionego zegara) i podpis ECDSA. Po 5 błędach w 10 min – blokada na 10 min.
//  * Nonce zmienia się po każdym poleceniu i przy każdym połączeniu (brak powtórzeń / replay).
#pragma once

#include <cstring>
#include <string>
#include <vector>

#include "esp_random.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "esphome/core/preferences.h"
#include "mbedtls/base64.h"
#include "mbedtls/bignum.h"
#include "mbedtls/ecdsa.h"
#include "mbedtls/ecp.h"
#include "mbedtls/sha256.h"

namespace brama_ble {

static const char *const TAG = "brama_ble";

constexpr uint32_t MAGIC = 0xB1E50002;
constexpr int MAX_KEYS = 16;
constexpr uint32_t NONCE_TTL_MS = 30000;
constexpr int FAIL_LIMIT = 5;
constexpr uint32_t FAIL_WINDOW_MS = 10 * 60 * 1000;
constexpr uint32_t LOCK_MS = 10 * 60 * 1000;
constexpr size_t CMD_LEN = 3 + 16 + 64;  // wersja, akcja, długość id, id (16 znaków hex), podpis r||s

struct Key {
  char dev[17];
  uint8_t pub[65];  // punkt nieskompresowany 0x04 || X || Y
  uint32_t from;    // 0 = bez ograniczenia
  uint32_t until;   // 0 = bez ograniczenia
};

struct Store {
  uint32_t magic;
  uint8_t count;
  char version[17];
  Key keys[MAX_KEYS];
};

static Store store_{};
static esphome::ESPPreferenceObject pref_;
static uint8_t nonce_[16];
static uint32_t nonce_ms_ = 0;
static bool nonce_valid_ = false;
static std::vector<uint8_t> buf_;
static int chunks_expected_ = 0;
static int chunks_got_ = 0;
static uint32_t fails_[FAIL_LIMIT] = {0};
static int fail_idx_ = 0;
static uint32_t locked_until_ = 0;
static std::string result_ = "GOTOWY";
static std::string last_dev_;
static std::string last_action_;

inline void load() {
  pref_ = esphome::global_preferences->make_preference<Store>(0xB1E5A002u);
  if (!pref_.load(&store_) || store_.magic != MAGIC || store_.count > MAX_KEYS) {
    memset(&store_, 0, sizeof(store_));
    store_.magic = MAGIC;
  }
  ESP_LOGI(TAG, "Wczytano %u kluczy BLE (wersja %s)", store_.count, store_.version);
}

inline int key_count() { return store_.count; }
inline std::string version() { return std::string(store_.version); }
inline const std::string &result() { return result_; }
inline const std::string &last_dev() { return last_dev_; }
inline const std::string &last_action() { return last_action_; }

// "dev,base64(65 B),from,until;dev,..." (pusty tekst = usuń wszystkie klucze)
inline int set_keys(const std::string &payload) {
  Store s{};
  s.magic = MAGIC;
  size_t pos = 0;
  while (pos < payload.size() && s.count < MAX_KEYS) {
    size_t end = payload.find(';', pos);
    if (end == std::string::npos) end = payload.size();
    std::string item = payload.substr(pos, end - pos);
    pos = end + 1;
    if (item.empty()) continue;
    size_t c1 = item.find(','), c2 = item.find(',', c1 + 1), c3 = item.find(',', c2 + 1);
    if (c1 != 16 || c2 == std::string::npos || c3 == std::string::npos) {
      ESP_LOGW(TAG, "Pominięto niepoprawny wpis klucza");
      continue;
    }
    Key &k = s.keys[s.count];
    memcpy(k.dev, item.data(), 16);
    k.dev[16] = 0;
    std::string b64 = item.substr(c1 + 1, c2 - c1 - 1);
    size_t olen = 0;
    if (mbedtls_base64_decode(k.pub, sizeof(k.pub), &olen,
                              reinterpret_cast<const unsigned char *>(b64.data()), b64.size()) != 0 ||
        olen != 65 || k.pub[0] != 0x04) {
      ESP_LOGW(TAG, "Pominięto klucz o złym formacie (%s)", k.dev);
      continue;
    }
    k.from = strtoul(item.substr(c2 + 1, c3 - c2 - 1).c_str(), nullptr, 10);
    k.until = strtoul(item.substr(c3 + 1).c_str(), nullptr, 10);
    s.count++;
  }
  // wersja = pierwsze 16 znaków hex SHA-256 całego tekstu (HA porównuje, czy trzeba wysłać ponownie)
  uint8_t h[32];
  mbedtls_sha256(reinterpret_cast<const unsigned char *>(payload.data()), payload.size(), h, 0);
  for (int i = 0; i < 8; i++) snprintf(&s.version[i * 2], 3, "%02x", h[i]);
  store_ = s;
  pref_.save(&store_);
  esphome::global_preferences->sync();
  ESP_LOGI(TAG, "Zapisano %u kluczy BLE (wersja %s)", store_.count, store_.version);
  return store_.count;
}

inline std::vector<uint8_t> new_nonce() {
  esp_fill_random(nonce_, sizeof(nonce_));
  nonce_ms_ = esphome::millis();
  nonce_valid_ = true;
  buf_.clear();
  chunks_expected_ = chunks_got_ = 0;
  return std::vector<uint8_t>(nonce_, nonce_ + sizeof(nonce_));
}

inline void fail_(const char *why) {
  result_ = why;
  uint32_t now = esphome::millis();
  fails_[fail_idx_] = now ? now : 1;
  fail_idx_ = (fail_idx_ + 1) % FAIL_LIMIT;
  int recent = 0;
  for (uint32_t t : fails_)
    if (t && now - t < FAIL_WINDOW_MS) recent++;
  if (recent >= FAIL_LIMIT) {
    locked_until_ = now + LOCK_MS;
    ESP_LOGW(TAG, "Za dużo błędnych prób – blokada BLE na 10 min");
  }
  ESP_LOGW(TAG, "Odrzucono polecenie BLE: %s", why);
}

inline bool locked_() {
  return locked_until_ && static_cast<int32_t>(locked_until_ - esphome::millis()) > 0;
}

inline bool verify_(const Key &k, const std::string &msg, const uint8_t *sig) {
  uint8_t hash[32];
  mbedtls_sha256(reinterpret_cast<const unsigned char *>(msg.data()), msg.size(), hash, 0);
  mbedtls_ecp_group grp;
  mbedtls_ecp_point q;
  mbedtls_mpi r, s;
  mbedtls_ecp_group_init(&grp);
  mbedtls_ecp_point_init(&q);
  mbedtls_mpi_init(&r);
  mbedtls_mpi_init(&s);
  int ret = mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_SECP256R1);
  if (ret == 0) ret = mbedtls_ecp_point_read_binary(&grp, &q, k.pub, sizeof(k.pub));
  if (ret == 0) ret = mbedtls_mpi_read_binary(&r, sig, 32);
  if (ret == 0) ret = mbedtls_mpi_read_binary(&s, sig + 32, 32);
  if (ret == 0) ret = mbedtls_ecdsa_verify(&grp, hash, sizeof(hash), &q, &r, &s);
  mbedtls_mpi_free(&r);
  mbedtls_mpi_free(&s);
  mbedtls_ecp_point_free(&q);
  mbedtls_ecp_group_free(&grp);
  return ret == 0;
}

// Kawałek polecenia: [nr][liczba kawałków][do 18 B danych].
// Zwraca 0 – czekam na resztę, -1 – odrzucone (powód w result()), 1 otwórz, 2 zamknij.
inline int on_chunk(const std::vector<uint8_t> &x, uint32_t now_ts, bool enabled) {
  if (x.size() < 3) {
    fail_("FORMAT");
    return -1;
  }
  int idx = x[0], total = x[1];
  if (idx == 0) {
    buf_.clear();
    chunks_got_ = 0;
    chunks_expected_ = total;
  }
  if (total < 1 || total > 8 || total != chunks_expected_ || idx != chunks_got_) {
    buf_.clear();
    chunks_expected_ = chunks_got_ = 0;
    fail_("FORMAT");
    return -1;
  }
  buf_.insert(buf_.end(), x.begin() + 2, x.end());
  chunks_got_++;
  if (chunks_got_ < chunks_expected_) return 0;

  std::vector<uint8_t> cmd;
  cmd.swap(buf_);
  chunks_expected_ = chunks_got_ = 0;
  bool nonce_ok = nonce_valid_ && esphome::millis() - nonce_ms_ < NONCE_TTL_MS;
  nonce_valid_ = false;  // jednorazowy niezależnie od wyniku

  if (!enabled) { result_ = "WYLACZONE"; return -1; }
  if (locked_()) { result_ = "BLOKADA"; return -1; }
  if (cmd.size() != CMD_LEN || cmd[0] != 1 || cmd[2] != 16) { fail_("FORMAT"); return -1; }
  int action = cmd[1];
  if (action != 1 && action != 2) { fail_("FORMAT"); return -1; }
  if (!nonce_ok) { fail_("NONCE"); return -1; }

  std::string dev(reinterpret_cast<const char *>(&cmd[3]), 16);
  const Key *key = nullptr;
  for (int i = 0; i < store_.count; i++)
    if (memcmp(store_.keys[i].dev, dev.data(), 16) == 0) key = &store_.keys[i];
  if (key == nullptr) { fail_("NIEZNANE"); return -1; }
  // Dostęp ograniczony w czasie wymaga ustawionego zegara (bez HA i internetu po restarcie ESP
  // zegara nie ma) – wtedy odrzucamy, żeby wygasły dostęp nie zadziałał. Klucze bez limitu działają zawsze.
  if ((key->from || key->until) && !now_ts) { result_ = "BRAK_CZASU"; return -1; }
  if (now_ts) {
    if (key->from && now_ts < key->from) { fail_("POZA_OKNEM"); return -1; }
    if (key->until && now_ts >= key->until) { fail_("WYGASLO"); return -1; }
  }
  char hex[33];
  for (int i = 0; i < 16; i++) snprintf(&hex[i * 2], 3, "%02x", nonce_[i]);
  const char *act = action == 1 ? "open" : "close";
  std::string msg = std::string("ble1|") + dev + "|" + hex + "|" + act;
  if (!verify_(*key, msg, &cmd[19])) { fail_("ZLY_PODPIS"); return -1; }

  result_ = "OK";
  last_dev_ = dev;
  last_action_ = act;
  ESP_LOGI(TAG, "Polecenie BLE %s od urządzenia %s", act, dev.c_str());
  return action;
}

}  // namespace brama_ble
