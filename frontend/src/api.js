/* TagMate 管理面 API 封装。所有请求走 /api 前缀，非 2xx 抛 Error（message 取后端 detail）。 */

async function request(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    const res = await fetch('/api' + path, opts);
    if (!res.ok) {
        let detail = res.statusText;
        try {
            const data = await res.json();
            detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data);
        } catch { /* 非 JSON 响应，保留 statusText */ }
        throw new Error(`${res.status} ${detail}`);
    }
    if (res.status === 204) return null;
    return res.json();
}

const get = (path) => request('GET', path);
const post = (path, body) => request('POST', path, body);
const put = (path, body) => request('PUT', path, body);
const del = (path) => request('DELETE', path);

function qs(params) {
    const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '');
    return entries.length ? '?' + new URLSearchParams(entries).toString() : '';
}

export default {
    listAgents: () => get('/agents'),
    getAgent: (id) => get(`/agents/${id}`),
    createAgent: (body) => post('/agents', body),
    updateAgent: (id, body) => put(`/agents/${id}`, body),
    deleteAgent: (id) => del(`/agents/${id}`),

    listBindings: (agentId) => get('/bindings' + qs({ agent_id: agentId })),
    createBinding: (body) => post('/bindings', body),
    updateBinding: (id, body) => put(`/bindings/${id}`, body),
    deleteBinding: (id) => del(`/bindings/${id}`),
    startBinding: (id) => post(`/bindings/${id}/start`),
    stopBinding: (id) => post(`/bindings/${id}/stop`),

    listInvocations: (params = {}) => get('/invocations' + qs(params)),
    getInvocation: (id) => get(`/invocations/${id}`),

    listTools: () => get('/tools'),
    listModels: () => get('/models'),
};
