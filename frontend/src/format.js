/* 展示用格式化工具。 */

/** 后端时间戳为 Unix 秒（REAL），转本地 "MM-DD HH:mm:ss"。空值返回 '-'。 */
export function fmtTime(ts) {
    if (ts === null || ts === undefined) return '-';
    const d = new Date(ts * 1000);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function truncate(s, n = 80) {
    if (!s) return '';
    return s.length > n ? s.slice(0, n) + '…' : s;
}

export const STATUS_LABELS = {
    connected: '已连接',
    connecting: '连接中',
    disconnected: '未连接',
    error: '错误',
    running: '运行中',
    completed: '完成',
    failed: '失败',
};

export function statusLabel(s) {
    return STATUS_LABELS[s] ?? s ?? '-';
}
