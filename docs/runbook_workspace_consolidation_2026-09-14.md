# Runbook: satukan workspace app ke checkout WSL

**Tanggal**: 14 Sep 2026. **Status**: langkah 1 selesai, langkah 2 menunggu the owner.
**Keputusan induk**: [`docs/proposal_wsl_windows_2026-08-03.md`](proposal_wsl_windows_2026-08-03.md) Track B, 3 Agustus. Runbook ini penegakannya, bukan keputusan baru.

## Kenapa

App ASB menjalankan routine di workspace mana pun yang dia buka. Dia kebuka di
`C:\Users\the owner\.gemini\antigravity\scratch\product-second-brain`, clone yang
sudah dipensiunkan 3 Agustus. Cron dan token ada di checkout WSL. Jadi router
menulis file thread dan branch request ke WSL, app membacanya di Windows, dan
tidak pernah ketemu. Itu sebabnya auto reply draft membuka sesi kosong: brief-nya
menunjuk file yang tidak ada di workspace tempat sesi itu jalan.

Pindah ke arah sebaliknya, menjadikan Windows sumber, sudah ditolak di opsi A2
dokumen Agustus: 20 baris cron repo ini plus seluruh pipeline You di crontab
yang sama, dan 23 file token di filesystem WSL. Ongkosnya besar untuk masalah
yang bukan soal platform. Masalahnya dua checkout, bukan OS-nya.

## Langkah 1: selamatkan isi clone Windows (SELESAI 14 Sep)

Dikerjakan lebih dulu karena konsolidasi tanpa ini akan menelantarkan pekerjaan
nyata, persis seperti 21 catatan yang terdampar pada Agustus.

- 23 deliverable hanya ada di clone Windows: dua PRD Example Program, canary cutover
  plan, decision pack, migration laundry list, weekly report 11 Sep, interview
  prep, dua draft Slack, dan script migration backlog. Nol di WSL, nol di origin.
- Dipindai kredensial lebih dulu, nol temuan, lalu di-commit dan di-push dari
  automation host: `fa3b98338`.
- `WAIT-0761` (Raouf Cherkawi, Work ID pipeline stages) hanya hidup di commit
  lokal clone Windows. Didaftarkan ulang lewat CLI, bukan cherry-pick, jadi
  propagasi ledger ikut jalan: `4449fb8a6`.
- Clone Windows dikembalikan ke `origin/main` setelah dipastikan dua commit
  lokalnya tidak memuat apa pun yang belum ada di origin.

Pelajaran yang perlu dicatat: selama langkah ini, hook auto-commit app merebut
`.git/index.lock` berulang kali dan satu rebase berhenti dengan konflik di
`waiting_on.json` dan `master_followup_tracker.md`. Dua penulis di satu repo
adalah masalah yang sedang dihapus runbook ini, dan dia sempat menggigit di
tengah jalan. Konflik itu jatuh di `waiting_on.json` dan di
[`journal/master_followup_tracker.md`](../journal/master_followup_tracker.md),
dua file yang dua-duanya tidak boleh di-merge tangan, jadi rebase-nya dibatalkan
dan isinya dipindahkan lewat CLI.

## Langkah 2: tes file watching lewat UNC (SEBELUM pindah)

Risiko yang belum terbukti: watcher app lewat `\\wsl.localhost\` bisa lebih
lambat atau tidak reliable dibanding disk NTFS lokal. Kalau benar bermasalah,
pindah ke Windows sebagai sumber baru masuk akal, dan kita kerjakan sadar ongkos.

Tes, di sesi app yang di-root ke path UNC:

1. Buka satu sesi app dengan workspace
   `\\wsl.localhost\Ubuntu\home\you\antigravity-projects\product-second-brain`.
2. Dari sesi WSL terpisah, tulis file ke `.asb/branches/requests/`. Sesi baru
   harus muncul dalam hitungan detik. Ini jalur yang dipakai auto reply draft,
   jadi ini yang paling penting.
3. Edit satu file `.md` dari luar app. App harus melihat isi barunya tanpa
   restart.
4. Jalankan `/autodraft run` dari dalam app. Branch request harus mendarat di
   checkout yang sama dengan yang dibaca app, dan sesinya harus bisa membuka
   file thread lewat path relatif.
5. Catat waktu tunggu tiap langkah. Lebih dari sekitar 30 detik untuk langkah 2
   berarti gagal.

## Langkah 3: pindah (setelah langkah 2 lulus)

**Ubah `root` workspace yang sudah ada. Jangan tambah workspace baru.** App
menyimpan `meta.workspaceId` di tiap sesi dan rail cuma menampilkan sesi yang
id-nya cocok dengan workspace aktif (`inActiveWorkspace` di `src/main.js`).
Workspace baru dapat id baru, jadi 651 sesi yang ada akan hilang dari sidebar
sampai kamu switch balik. Id dipertahankan, path diganti, riwayat ikut.

Dikerjakan oleh `C:\Users\the owner\Desktop\Add-WSL-workspace.bat`, yang menolak
jalan selama app masih terbuka dan membuat backup sebelum menulis.

1. Tutup app sepenuhnya.
2. Klik dua kali `Add-WSL-workspace.bat`. Dia mengubah root dan membuka app lagi.
3. Pastikan routine `reply-router` masih terdaftar dan Scheduled runs menyala.
4. Jalankan `python3 .agent/scripts/automation_settings.py held`. Harus keluar 0
   dan tidak menyebut `parent_off`.
5. Pantau satu hari penuh. Hentikan kalau ada routine yang mulai diam.

## Langkah 4: pensiunkan clone lama

Jangan hapus di hari yang sama. Beri jeda minimal satu minggu penuh setelah
langkah 3 lulus, supaya kalau ada yang terlewat masih bisa diambil.

1. `git status --porcelain` di clone lama harus bersih selain `journal/state`.
2. Bandingkan sekali lagi terhadap `origin/main` dengan cara yang sama seperti
   langkah 1, karena app mungkin masih menulis ke sana sampai langkah 3.
3. Baru arsipkan atau hapus.

## Yang sudah diperbaiki di sisi kode

- Brief branch sekarang memakai path relatif, bukan link absolut milik mesin
  router. Brief jadi portabel antar checkout.
- `automation_settings.py` membaca `runs.jsonl` dari semua checkout, dengan dua
  ejaan untuk folder Windows yang sama (`C:/...` dan `/mnt/c/...`), karena proses
  WSL tidak bisa membuka yang pertama dan proses Windows tidak bisa membuka yang
  kedua. Sebelum ini detektor melaporkan "tidak pernah jalan" tentang routine
  yang baru saja jalan.
