-- listing board dimension; WATCHLIST trades under FCA call-auction mechanics
select board, board_name_id, is_fca
from (values
    ('MAIN', 'Papan Utama', false),
    ('DEVELOPMENT', 'Papan Pengembangan', false),
    ('ACCELERATION', 'Papan Akselerasi', false),
    ('NEW_ECONOMY', 'Papan Ekonomi Baru', false),
    ('WATCHLIST', 'Papan Pemantauan Khusus', true)
) as t(board, board_name_id, is_fca)
