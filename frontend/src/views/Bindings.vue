<template>
    <div class="view">
        <div class="view-header">
            <h2>Bindings</h2>
            <span class="spacer"></span>
            <button class="btn" @click="load">刷新</button>
        </div>

        <p v-if="error" class="form-error">{{ error }}</p>
        <div v-else-if="loaded && bindings.length === 0" class="empty-state">还没有 binding。到 Agent 编辑页添加。</div>

        <table v-else-if="loaded" class="table">
            <thead>
                <tr>
                    <th>Agent</th>
                    <th>平台</th>
                    <th>App ID</th>
                    <th>状态</th>
                    <th>连接时间</th>
                    <th class="col-num">消息数</th>
                    <th>最近错误</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                <tr v-for="b in bindings" :key="b.id">
                    <td class="col-name">
                        <RouterLink :to="`/agents/${b.agent_id}`">{{ agentName(b.agent_id) }}</RouterLink>
                    </td>
                    <td>{{ b.platform }}</td>
                    <td class="col-mono">{{ b.credentials.app_id }}</td>
                    <td><StatusDot :status="b.status" /></td>
                    <td class="col-muted">{{ fmtTime(b.connected_at) }}</td>
                    <td class="col-num">{{ b.message_count }}</td>
                    <td class="col-error" :title="b.last_error || ''">{{ b.last_error || '-' }}</td>
                    <td class="col-actions">
                        <button v-if="b.status === 'connected' || b.status === 'connecting'"
                                class="btn sm" :disabled="busy[b.id]" @click="stop(b)">stop</button>
                        <button v-else class="btn sm" :disabled="busy[b.id]" @click="start(b)">start</button>
                    </td>
                </tr>
            </tbody>
        </table>
    </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import api from '../api.js';
import StatusDot from '../components/StatusDot.vue';
import { fmtTime } from '../format.js';

const POLL_MS = 5000;

const bindings = ref([]);
const agents = ref({});
const busy = reactive({});
const loaded = ref(false);
const error = ref('');
let pollTimer = null;

function agentName(id) {
    return agents.value[id] ?? id;
}

async function load() {
    try {
        const [bs, as] = await Promise.all([api.listBindings(), api.listAgents()]);
        bindings.value = bs;
        agents.value = Object.fromEntries(as.map((a) => [a.id, a.name]));
        error.value = '';
    } catch (e) {
        error.value = e.message;
    } finally {
        loaded.value = true;
    }
}

async function run(b, fn) {
    busy[b.id] = true;
    try {
        await fn(b.id);
        await load();
    } catch (e) {
        error.value = e.message;
    } finally {
        busy[b.id] = false;
    }
}

const start = (b) => run(b, api.startBinding);
const stop = (b) => run(b, api.stopBinding);

onMounted(() => {
    load();
    pollTimer = setInterval(load, POLL_MS);
});
onBeforeUnmount(() => clearInterval(pollTimer));
</script>
