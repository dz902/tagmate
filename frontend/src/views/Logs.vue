<template>
    <div class="logs-view">
        <div class="logs-list">
            <div class="logs-toolbar">
                <span class="muted">Agent</span>
                <select v-model="agentFilter" @change="load">
                    <option value="">全部</option>
                    <option v-for="a in agents" :key="a.id" :value="a.id">{{ a.name }}</option>
                </select>
                <button class="btn sm" @click="load">刷新</button>
                <span v-if="error" class="error-text">{{ error }}</span>
            </div>

            <div class="logs-table-wrap">
                <div v-if="loaded && invocations.length === 0" class="empty-state">暂无 invocation。</div>
                <table v-else class="table">
                    <thead>
                        <tr>
                            <th>开始时间</th>
                            <th>Agent</th>
                            <th>场景</th>
                            <th>用户</th>
                            <th>输入</th>
                            <th>状态</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="inv in invocations" :key="inv.id" class="clickable"
                            :class="{ selected: inv.id === selectedId }" @click="select(inv.id)">
                            <td class="col-muted col-mono">{{ fmtTime(inv.started_at) }}</td>
                            <td class="col-name">{{ agentName(inv.agent_id) }}</td>
                            <td><span class="kind-tag" :class="inv.scene">{{ inv.scene }}</span></td>
                            <td class="col-mono">{{ principalLabel(inv.principal_id) }}</td>
                            <td class="col-input" :title="inv.input_text">{{ truncate(inv.input_text, 60) }}</td>
                            <td><StatusDot :status="inv.status" /></td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <aside v-if="detail" class="logs-detail">
            <div class="detail-header">
                <span class="kind-tag" :class="detail.scene">{{ detail.scene }}</span>
                <span>{{ agentName(detail.agent_id) }}</span>
                <span class="location">{{ detail.id }}</span>
                <span class="spacer"></span>
                <button class="btn sm" @click="detail = null; selectedId = ''">关闭</button>
            </div>

            <dl class="detail-meta">
                <dt>状态</dt><dd><StatusDot :status="detail.status" /></dd>
                <dt>用户</dt><dd>{{ principalLabel(detail.principal_id) }}</dd>
                <dt>开始</dt><dd>{{ fmtTime(detail.started_at) }}</dd>
                <dt>结束</dt><dd>{{ fmtTime(detail.finished_at) }}</dd>
                <dt>Session</dt><dd class="mono">{{ detail.session_id }}</dd>
                <dt>Chat</dt><dd class="mono">{{ detail.chat_id }}</dd>
            </dl>

            <div class="detail-section">
                <h4>输入</h4>
                <div class="detail-text">{{ detail.input_text }}</div>
            </div>

            <div class="detail-section">
                <h4>Events</h4>
                <div class="timeline">
                    <div v-if="detail.events.length === 0" class="muted">无事件</div>
                    <div v-for="ev in detail.events" :key="ev.id" class="timeline-item" :class="ev.type">
                        <span class="kind-tag" :class="ev.type">{{ ev.type }}</span>
                        <div class="body">
                            <template v-if="ev.type === 'tool_call'">
                                <div class="name">{{ ev.payload.name }}</div>
                                <JsonBlock title="input" :value="ev.payload.input" />
                            </template>
                            <template v-else-if="ev.type === 'tool_result'">
                                <div class="name">
                                    <StatusDot :status="ev.payload.status === 'success' ? 'completed' : 'failed'" :label="false" />
                                    {{ ev.payload.status }}
                                </div>
                                <JsonBlock title="content" :value="ev.payload.content" />
                            </template>
                            <template v-else-if="ev.type === 'assistant'">
                                <div class="text">{{ ev.payload.text }}</div>
                            </template>
                            <template v-else-if="ev.type === 'error'">
                                <div class="text">{{ ev.payload.type }}: {{ ev.payload.error }}</div>
                            </template>
                            <template v-else>
                                <JsonBlock :title="ev.type" :value="ev.payload" />
                            </template>
                            <div v-if="ev.principal_id" class="principal">使用 {{ principalLabel(ev.principal_id) }} 的身份</div>
                        </div>
                        <span class="time">{{ fmtTime(ev.created_at).slice(6) }}</span>
                    </div>
                </div>
            </div>

            <div class="detail-section">
                <h4>输出</h4>
                <div v-if="detail.error" class="detail-text error-text">{{ detail.error }}</div>
                <div v-else class="detail-text">{{ detail.output_text ?? '-' }}</div>
            </div>
        </aside>
    </div>
</template>

<script setup>
/* Logs：左侧 invocation 列表 + 右侧详情（input -> events 时间线 -> output）。
   列表接口只返回 principal_id；详情里拿到 principal 后缓存到 principals，回填列表显示名。 */
import { onMounted, reactive, ref } from 'vue';
import api from '../api.js';
import JsonBlock from '../components/JsonBlock.vue';
import StatusDot from '../components/StatusDot.vue';
import { fmtTime, truncate } from '../format.js';

const agents = ref([]);
const agentFilter = ref('');
const invocations = ref([]);
const selectedId = ref('');
const detail = ref(null);
const principals = reactive({});
const loaded = ref(false);
const error = ref('');

function agentName(id) {
    return agents.value.find((a) => a.id === id)?.name ?? id;
}

function principalLabel(id) {
    if (!id) return '-';
    const p = principals[id];
    if (!p) return id;
    return p.display_name ? `${p.display_name} (${p.user_id})` : p.user_id;
}

async function load() {
    try {
        invocations.value = await api.listInvocations({ agent_id: agentFilter.value, limit: 100 });
        error.value = '';
    } catch (e) {
        error.value = e.message;
    } finally {
        loaded.value = true;
    }
}

async function select(id) {
    selectedId.value = id;
    try {
        const inv = await api.getInvocation(id);
        if (inv.principal) principals[inv.principal.id] = inv.principal;
        detail.value = inv;
    } catch (e) {
        error.value = e.message;
    }
}

onMounted(async () => {
    try {
        agents.value = await api.listAgents();
    } catch (e) {
        error.value = e.message;
    }
    await load();
});
</script>
