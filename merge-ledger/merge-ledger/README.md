# Merge Ledger

Alat ukur untuk pertanyaan yang tidak dijawab alat review mana pun: **apa yang
terjadi pada kode setelah ia di-merge?**

Baca riwayat git sebuah repositori, lalu jawab empat hal:

| Angka | Artinya |
|---|---|
| **Ditulis ulang cepat** | Berapa persen baris baru yang sudah diubah lagi dalam 14 hari. Ukuran kode yang belum matang saat masuk. |
| **Salin vs pindah** | Perbandingan baris yang menyalin blok lain terhadap baris yang dipindah untuk konsolidasi. Di atas 1× artinya tim lebih banyak menyalin daripada merapikan. |
| **Kode kembar di HEAD** | Berapa persen kode saat ini berada di dalam blok yang muncul lebih dari sekali. |
| **Penyamaran error** | Konstruksi yang menelan kegagalan diam-diam, per 1000 baris. |

Ditambah dua hal yang membuat angka di atas berarti:

- **Arah** — tiap pemindaian disimpan, jadi laporan berikutnya bisa bilang
  "11%, turun dari 19% sebulan lalu" alih-alih sekadar "11%".
- **Antrian review** — berapa PR sedang menunggu, berapa lama sampai reviewer
  pertama menyentuhnya, dan apakah PR bertanda agen menunggu lebih lama.
  Bersifat pilihan, lewat GitHub.

Tidak ada layanan, tidak ada akun, tidak ada data yang keluar dari mesin.
Hanya `git` dan Python 3.10+. Nol dependensi.

## Pasang

```bash
git clone <repo-ini> && cd mergeledger
pip install -e .
```

Atau jalankan langsung tanpa memasang:

```bash
python3 -m mergeledger /path/ke/repo
```

## Pakai

```bash
# laporan 90 hari terakhir
mergeledger .

# rentang lebih panjang, simpan juga sebagai JSON
mergeledger ~/kerja/backend --days 180 --json hasil.json

# repo besar: lewati pemindaian duplikasi seluruh repo
mergeledger . --no-head-scan

# ambang churn lebih longgar
mergeledger . --churn-days 30

# tanpa jaringan sama sekali
mergeledger . --no-github
```

Keluarannya satu berkas HTML mandiri — tanpa CDN, tanpa font eksternal, tanpa
permintaan jaringan. Bisa dilampirkan di email, disimpan sebagai artefak CI,
atau dibuka dari laptop siapa pun secara offline.

Ringkasan di terminal:

```
backend · main · 90 hari · 412 commit

  !  ditulis ulang           11.3%   waspada
  !! salin vs pindah          2.40×  perhatian
  ·  kode kembar              6.8%   baik
  !  penyamaran error    3.10/kloc   waspada
  !  antri review            34 PR   waspada
  !  waktu ke merge        2.5 hari  waspada
  !  tunggu reviewer       19.4 jam  waspada

  dibanding 30 hari lalu:
     Ditulis ulang              9.8 -> 11.3%  (+15%) memburuk
     Waktu tunggu review       12.1 -> 19.4 jam  (+60%) memburuk
```

## Arah perubahan

Tiap pemindaian disimpan ke `.merge-ledger/history.json` di dalam repositori.
Berkas ini kecil, berformat JSON biasa, dan **sebaiknya ikut di-commit** supaya
seluruh tim membaca garis dasar yang sama.

Dua pengaman supaya trennya jujur:

- Perbandingan hanya dilakukan antar pemindaian dengan **cabang, jendela waktu,
  dan ambang churn yang sama**. Kalau tidak, "perbaikan" bisa muncul hanya
  karena pengaturannya diubah.
- Perubahan di bawah **5% relatif** ditandai tetap, bukan naik atau turun.
  Tanpa ini, tiap pemindaian akan terlihat bergerak padahal hanya derau.

Matikan dengan `--no-history`, atau pindahkan dengan `--history <path>`.

## Antrian review

Riwayat git tahu apa yang terjadi pada kode setelah masuk. Yang tidak
diketahuinya: berapa lama kode itu menunggu di depan pintu. Bagian ini
mengambilnya dari GitHub.

```bash
export GITHUB_TOKEN=ghp_xxx      # atau pakai --token
mergeledger .
```

| Tanpa token | Dengan token |
|---|---|
| Jumlah PR menunggu, berapa yang basi (>14 hari), yang tertua | Semua yang di kiri |
| Waktu dari buka sampai merge | **Waktu tunggu sampai reviewer pertama menyentuh** |
| Tingkat penerimaan | Berapa PR di-merge tanpa satu pun review |
| Perbandingan lama tunggu PR agen vs manusia | |

Waktu tunggu reviewer butuh satu panggilan API per PR, karena itu hanya
dilakukan bila ada token. Tanpa token, kuota GitHub habis di 60 panggilan
per jam.

Bagian ini **tidak pernah menggagalkan pemindaian**. Kalau tidak ada jaringan,
tokennya salah, repositorinya privat, atau remote-nya bukan GitHub, laporan
tetap terbit dengan alasan yang jelas tertulis. Matikan dengan `--no-github`.

## Gerbang CI

Setiap ambang bersifat pilihan. Tanpa flag, alat ini hanya melapor.

```bash
mergeledger . --max-churn 15 --max-duplication 20 --max-stale-prs 10 --quiet
```

Keluar dengan kode 1 bila terlampaui. Contoh alur kerja GitHub Actions ada di
`examples/merge-ledger.yml`.

Saran: **jangan pasang gerbang di minggu pertama.** Jalankan dalam mode lapor
selama sebulan dulu untuk tahu angka dasar tim sendiri, baru tetapkan ambang
sedikit di atasnya. Ambang yang dipinjam dari rata-rata industri hampir selalu
salah untuk repositori tertentu.

## Cara angka ini dihitung

**Ditulis ulang (churn).** Untuk tiap baris yang dihapus atau diubah, alat
bertanya ke `git blame` berapa umur baris itu. Kalau di bawah ambang (bawaan 14
hari), baris itu dihitung. Penyebutnya seluruh baris baru dalam rentang waktu.
Churn dibebankan ke **penulis asal** baris, bukan ke orang yang menghapusnya —
kalau kode agen ditulis ulang seorang manusia tiga hari kemudian, itu tercatat
sebagai churn kode agen.

**Salin-tempel.** Dalam satu commit, blok lima baris atau lebih yang muncul
lebih dari sekali di antara baris baru dihitung sebagai salinan. Kemunculan
pertama dianggap asli.

**Pemindahan.** Baris yang hilang dari satu file dan muncul di file lain dalam
commit yang sama. Ini tanda konsolidasi — indikator refactoring yang sehat.

**Kode kembar di HEAD.** Berbeda dari tiga di atas yang melihat *perubahan*,
ini memindai *kondisi saat ini*: berapa persen baris berada di dalam blok lima
baris yang berulang di seluruh repositori.

**Penyamaran error.** Deteksinya sadar konteks. `pass` yang berdiri sendiri
tidak dihitung — hanya `pass` tepat setelah blok penangkap. Penekan alat statis
(`# noqa`, `@ts-ignore`, `// nolint`) dihitung di ember terpisah, karena
sifatnya berbeda: itu mematikan peringatan, bukan menelan kegagalan.

**Yang tidak dihitung.** Kode pihak ketiga, hasil build, berkas terkunci,
migrasi, dan berkas yang dihasilkan mesin dikeluarkan dari semua perhitungan
(lihat `filters.py`). File tes tetap dihitung tapi porsinya dilaporkan terpisah.

**Jendela waktu.** `git log --since` menyaring berdasarkan tanggal commit,
sedangkan umur baris dihitung dari tanggal penulisan. Di repo dengan alur patch
keduanya bisa terpaut berbulan-bulan, jadi alat ini menjaring lebih lebar lalu
menyaring sendiri berdasarkan tanggal penulisan.

## Menandai kode buatan AI

Perbandingan AI vs manusia hanya bisa dihitung kalau ada jejaknya. Alat ini
mengenali trailer `Co-Authored-By` dari Claude, Copilot, Cursor, Devin, Aider,
Codex, dan Gemini, serta nama author yang mengandung penanda bot.

Kalau tim pakai konvensi sendiri:

```bash
mergeledger . --ai-pattern 'assisted-by:\s*agen' --ai-pattern '\[ai\]'
```

Tanpa penanda apa pun, laporan tetap berjalan — angkanya berlaku untuk seluruh
kode tanpa pembedaan, dan laporan akan menyebutkan hal itu secara eksplisit
alih-alih diam.

## Batas alat ini

Ini bagian yang paling penting dibaca.

**Alat ini membaca riwayat git, bukan maksud penulisnya.** Churn tinggi bisa
berarti kode buru-buru — tapi bisa juga berarti tim sedang bereksperimen dengan
sehat di area yang memang belum jelas bentuknya. Prototipe yang ditulis ulang
lima kali dalam seminggu adalah tanda proses yang benar, bukan salah. Angka di
sini bahan percakapan, bukan vonis.

**Sengaja tidak ada metrik per individu.** Semua agregasi berhenti di level
area dan file. Tabel file menampilkan *jumlah* penulis, bukan namanya. Ini
keputusan produk, bukan keterbatasan teknis: metrik developer yang bisa
diperingkat akan disabotase, dan sudah sepantasnya.

**Deteksi duplikasi bersifat leksikal.** Blok yang identik secara teks akan
tertangkap; duplikasi logika yang ditulis berbeda tidak.

**Blame tidak menembus rename yang tidak terdeteksi git.** Kalau file dipindah
sekaligus diubah besar-besaran, sebagian riwayatnya bisa terputus dan churn
tampak lebih rendah dari kenyataan.

**Angka ambang bawaan adalah titik awal, bukan kebenaran.** Lihat
`scoring.py` — semuanya di satu tempat dan sengaja dibuat mudah diubah.

**Penanda PR agen bersifat tebakan.** Deteksinya membaca nama cabang, label,
judul, dan nama author. Tim yang tidak menandai apa pun akan mendapat angka
nol, dan itu jujur — bukan berarti tidak ada kode agen, berarti jejaknya tidak
ada.

## Struktur

```
mergeledger/
  gitio.py      pembungkus perintah git dan pengurai keluarannya
  filters.py    file mana yang dihitung, dan siapa penulis sebuah commit
  metrics.py    perhitungan churn, duplikasi, pemindahan, penyamaran
  scoring.py    skor risiko per area — semua ambang ada di sini
  history.py    penyimpan riwayat dan pembanding antar pemindaian
  github.py     antrian review — bersifat pilihan, gagal dengan tenang
  report.py     laporan HTML mandiri dan keluaran JSON
  cli.py        antarmuka baris perintah
tests/
  test_metrics.py   perhitungan git terhadap repo sintetis
  test_flow.py      antrian review dan riwayat, tanpa jaringan
```

Pemanggilan git dijalankan paralel per kelompok commit. Di mesin empat inti,
repositori 1500 commit selesai dalam hitungan puluhan detik. Atur dengan
`--workers`.

Jalankan uji:

```bash
python3 -m unittest discover -s tests -v
```

46 uji, seluruhnya tanpa jaringan. Uji git membangun repositori kecil yang
isinya dirancang supaya jawabannya bisa dihitung tangan — 10 baris churn, 8
baris pindah, 6 baris salinan — lalu memastikan alat mengeluarkan angka yang
sama. Uji antrian review memakai data PR palsu dengan waktu yang ditentukan,
termasuk jalur gagalnya: batas laju, repo privat, dan tanpa koneksi.

## Lisensi

MIT.
