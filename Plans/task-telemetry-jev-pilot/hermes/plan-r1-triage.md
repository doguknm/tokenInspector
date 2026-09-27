# Plan review r1 — triage (2026-09-27)

Sources: `task-telemetry-jev-pilot-plan-r1-A.md` (20 findings), `task-telemetry-jev-pilot-plan-r1-B.md` (16 findings).
Duplicates merged → 30 items in 7 groups. "Öneri" = Claude's recommendation; the user decides.

## G1 — Gizlilik ve saklama
| # | Bulgular | Sorun (sade) | Senaryo | Öneri |
|---|---|---|---|---|
| 1 | A-F1, B-F2 | Prompt, eklentinin disk kuyruğuna temizlenmeden yazılıyor | Gönderim başarısız → içinde şifre geçen prompt diske düz yazılır | Düzelt: aynı temizleyici eklentide de çalışsın (kuyruğa yazmadan önce), kuyruk dosyası 600 izinli, kuyrukta "canary" testi |
| 2 | A-F2 | Temizleyici bazı yasak kategorileri kapsamıyor | `/srv/...` gibi mutlak yol, iç hostname, tam git remote URL'i JEV'e gider | Düzelt: bu kategoriler için desen + test |
| 3 | A-F3, B-F1 | 30 günlük silme yedekleri ve WAL'ı kapsamıyor | Silinen prompt `.bak-v*` yedeğinde ya da WAL sayfalarında okunur kalır | Düzelt: 30 günden eski yedekleri sil/yeniden temizle, purge sonrası `wal_checkpoint(TRUNCATE)`, "mantıksal silme ≠ fiziksel silme" belgelensin |
| 4 | A-F4 | Saklama süresi yeniden gönderimde sıfırlanıyor; süresi dolmuş metin purge'e kadar okunabiliyor | Kuyrukta 3 gün bekleyen prompt 30+3 gün kalır; süresi dolmuş prompt JEV'e gider | Düzelt: üreticinin yakalama zamanı taşınsın, okuma/seçim/JEV öncesi süre kontrolü |
| 5 | A-F12 | Geri alma (rollback) sonrası purge çalışmıyor | Eski sürüme dönülür, promptlar süresiz kalır | Düzelt: rollback runbook'u — yakalamayı kapat + tek seferlik purge betiği |
| 6 | A-F20 | Etiketleme aracında terminal kontrol karakterleri; notlarda hassas metin | Prompt içindeki kaçış dizisi terminali bozar; not alanına kopyalanan sır süresiz kalır | Düzelt: kontrol karakterlerini kaçır, not uzunluğu sınırı + aynı 30 gün kuralı |
| 7 | B-F4 | Üçüncü tarafa veri gönderimi için onay kapısı yok | Bayraklar onaydan önce açılır, regex'in kaçırdığı gizli metin JEV'e (ZDR yok) gider | Düzelt: status.md'de aktivasyon kapısı — veri kategorileri, iki sağlayıcı, kalan risk ADR'de kullanıcı onayıyla kayıtlı olmadan bayrak açılmaz |

## G2 — Yetkilendirme
| 8 | A-F5, B-F3 | `INGEST_TOKEN` yoksa hassas uçlar açık | Token tanımsız + prompt saklama açık → yerel her süreç promptları okur, etiketleri değiştirir, JEV bütçesini harcar | Düzelt: prompt saklama veya JEV açıkken token zorunlu (yoksa servis bu özellikleri başlatmaz), Host/Origin kontrolü, testler |

## G3 — JEV worker ve bütçe
| 9 | A-F6 | Bütçe her çağrıdan önce kalıcı ayrılmıyor | Retry'lar sayılmadan önce süreç çöker → harcama eksik sayılır | Düzelt: her çağrıdan önce kalıcı rezervasyon, sonra mutabakat |
| 10 | A-F7 | Token tahmini üst sınır değil | Gerçek token tahmini aşar → "sert tavan" delinir | Düzelt: muhafazakâr üst sınır + gateway'in bildirdiği gerçek maliyetle mutabakat |
| 11 | A-F8 | `/evaluate` eşzamanlılık yarışı | İki istek aynı anda kabul edilir → iki çalışma | Düzelt: istek içinde atomik rezervasyon + run id + durum (queued/running/done) |
| 12 | A-F9, B-F13 | Retry-After 30 sn ile kırpılıyor | Sağlayıcı 120 sn der, worker 30 sn'de tekrar dener | Düzelt: Retry-After'a uy; izin verilen bekleme aşılırsa çalışmayı durdur/ertele; HTTP-date testleri |
| 13 | A-F16 | Cevap doğrulaması eksik | Negatif olasılık veya NaN rapora girer | Düzelt: sonlu ve sınırlı değerler, 5 seviye, geçerli legend |
| 14 | B-F14 | Parser gerçek cevap örneği olmadan yazılacak | Uydurma alan yolları test edilir, gerçek cevapta bozulur | Düzelt: 27 Eylül testinde alınan gerçek cevap (sır içermiyor) fixture olarak plana eklensin |

## G4 — Eklenti kuyruğu
| 15 | A-F10 | Reddedilen paket kuyruğu tıkar | Kuyruktaki bir paket 422 alır, her flush orada durur | Düzelt: kalıcı red → ayrı "dead-letter" klasörü + sayaç; bozuk dosya işleme |
| 16 | A-F11 | Çökme dayanıklılığı (fsync) tanımsız | Elektrik kesintisinde "kalıcı" paket kaybolur | Düzelt: dosya + klasör fsync, bellekteki kayıp sınırı belgelensin |
| 17 | A-F19 | Backend hazır olmadan prompt yakalama başlarsa prompt kaybolur; bileşim için AC yok | Eski backend prompt alanını yok sayar → pilot içeriği kalıcı kaybolur | Düzelt: eklenti backend şema sürümünü (v10) görmeden yakalamayı açmaz + bileşim/parent-root AC'leri |

## G5 — Task tanımı ve pilot geçerliliği
| 18 | A-F13, B-F16 | "Bir task_id = bir kullanıcı promptu" varsayımı ve hook alanları doğrulanmamış | Bir task_id birden çok promptu kapsar ya da alt task'lar kök görünüp pilota girer | Düzelt: implementasyon öncesi "keşif adımı" (sahibi + kanıt), hiyerarşi bilinmiyorsa `unknown` ve örneklemden hariç |
| 19 | A-F14 | Karmaşıklık kaynağı ve sıralama belirsiz | request-shape-v1 olmayan değer öyle etiketlenir; eşit zaman damgasında backfill ile canlı akış farklı sonuç verir | Düzelt: kaynak (method) alanı zorunlu + zaman+event-id deterministik sıralama |
| 20 | A-F17 | Geçmiş veri pilotu besleyemez; hareketsizlik ≠ bitiş | Geçmiş task'larda prompt yok; 1 saat bekleyen task "bitti" sayılır | Düzelt: AC4 "sadece metadata" olarak düzeltilsin, pilot ileriye dönük; bitiş sinyali = Hermes oturum sonu/sonraki task başlangıcı, yoksa 60 dk hareketsizlik "tahmini bitiş" etiketiyle |
| 21 | A-F18, B-F15 | Metrikler aynı örneklem üzerinde değil; tanımsız sonuç yok | JEV'in başarısız olduğu zor task'lar sadece JEV'den düşer; sabit etiketlerde κ tanımsız | Düzelt: eşleşmiş tam vakalar, "yetersiz kanıt" sonucu, en az 40 çift, hariç tutulanlar raporlanır |

## G6 — Migration ve test sağlamlığı
| 22 | A-F15, B-F12 | Toplam kontrolleri satır değişikliğini kanıtlamıyor; yedek doğrulaması geç | Değişiklikler birbirini götürür, SUM aynı kalır; bozuk yedek migration'dan sonra fark edilir | Düzelt: tüm eski sütunlar satır bazında karşılaştırılsın, yedek bütünlüğü migration'dan önce, eski uygulama v10 DB'de başlatılıp test edilsin |
| 23 | B-F10 | Test `prompt_*` sütunlarının hepsi NULL diyor ama `prompt_truncated` NOT NULL 0 | Doğru kod test'te düşer | Düzelt: testi düzelt |
| 24 | B-F11 | v9 fixture gerçek v9 değil | Yeni sütunlar migration'dan önce var → test yanlış nedenle geçer | Düzelt: sabitlenmiş gerçek v9 şeması |

## G7 — Frontend ve E2E
| 25 | B-F5 | Grafik 200 kayıtla sınırlı | 200'den fazla puanlı task'ta eskiler sessizce düşer | Düzelt (basit): grafikte "son 200 gösteriliyor" etiketi + test (pilot ~50 task) |
| 26 | B-F6 | Eski cevap yeni filtreyi ezebilir | Yavaş eski istek sonra gelir, ekran filtreyle uyuşmaz | Düzelt: istek sayacı ile eski cevabı yok say |
| 27 | B-F7 | Boş sayfa ve sabit sıralama tanımsız | "Sayfa 1 / 0"; eşit zamanlı satırlar sayfalar arasında kayar | Düzelt: boş durum metni + task_ref ikincil sıralama |
| 28 | B-F8 | E2E bazı ekran durumlarını test etmiyor | Çalışıyor/bütçe uyarısı/404 bozuk olsa da testler geçer | Düzelt: sabit fixture'larla bu durumlar test edilsin |
| 29 | B-F9 | XSS testi zararsız veriyle yapılıyor | Kötü niyetli task_id çalışır, test fark etmez | Düzelt: saldırgan dizgelerle test, gizli DOM ve istekler de kontrol |
| 30 | B (UNSURE) | README/CHANGELOG/ARCHITECTURE güncellemesinin sahibi yok | Belgeler eksik kalır | Düzelt: status.md'de bakım satırı |
