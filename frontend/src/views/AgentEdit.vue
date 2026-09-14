<template>
    <div class="view">
        <div class="view-header">
            <h2>{{ isNew ? '新建 Agent' : (form.name || 'Agent') }}</h2>
            <span class="spacer"></span>
            <button class="btn" @click="router.push('/agents')">返回</button>
        </div>

        <div v-if="loadError" class="form-error">{{ loadError }}</div>

        <form v-else class="form" @submit.prevent="save">
            <div class="form-row">
                <label>名称</label>
                <div class="form-control"><input v-model.trim="form.name" type="text" required placeholder="agent 名称"></div>
            </div>
            <div class="form-row">
                <label>模型</label>
                <div class="form-control">
                    <select v-model="form.model">
                        <option v-for="m in models" :key="m.id" :value="m.id">{{ m.name }}</option>
                    </select>
                </div>
            </div>
            <div class="form-row">
                <label>Instructions</label>
                <div class="form-control"><textarea v-model="form.instructions" placeholder="system prompt"></textarea></div>
            </div>
            <div class="form-row">
                <label>Tools</label>
                <div class="form-control check-list">
                    <span v-if="tools.length === 0" class="muted">无可用工具</span>
                    <label v-for="t in tools" :key="t.name" class="check-item">
                        <input v-model="form.tools" type="checkbox" :value="t.name">
                        <span class="mono">{{ t.name }}</span>
                        <span v-if="t.requires_user_credentials" class="hint">仅 DM</span>
                    </label>
                </div>
            </div>
            <div class="form-row">
                <label>Connections</label>
                <div class="form-control check-list">
                    <label v-for="c in CONNECTIONS" :key="c" class="check-item">
                        <input v-model="form.connections" type="checkbox" :value="c">
                        <span>{{ c }}</span>
                    </label>
                </div>
            </div>
            <p v-if="saveError" class="form-error">{{ saveError }}</p>
            <div class="form-actions">
                <button class="btn primary" type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button>
                <span class="spacer"></span>
                <button v-if="!isNew" class="btn danger" type="button" @click="remove">删除</button>
            </div>
        </form>

        <!-- Bindings -->
        <template v-if="!isNew && !loadError">
            <h3 class="section-title">Bindings</h3>

            <div v-if="bindings.length === 0" class="muted" style="margin-bottom: 12px">尚未绑定任何 bot。</div>
            <table v-else class="table" style="margin-bottom: 16px">
                <thead>
                    <tr>
                        <th>机器人</th>
                        <th>状态</th>
                        <th class="col-num">消息数</th>
                        <th>最近错误</th>
                        <th>启用</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="b in bindings" :key="b.id">
                        <td class="col-bot">
                            <span class="bot-identity">
                                <img v-if="b.meta?.avatar_url" :src="b.meta.avatar_url" class="bot-avatar">
                                <span>{{ b.meta?.bot_name || b.platform }}</span>
                            </span>
                            <span class="bot-sub muted">{{ b.credentials.app_id }}<template v-if="b.meta?.tenant_name"> · {{ b.meta.tenant_name }}</template><template v-else-if="b.meta?.tenant_auth_url"> · <a :href="b.meta.tenant_auth_url" target="_blank" rel="noopener" title="应用缺少 tenant:tenant:readonly 权限，开通后重启 binding 即可显示企业名">开通企业信息权限</a></template></span>
                        </td>
                        <td><StatusDot :status="b.status" /></td>
                        <td class="col-num">{{ b.message_count }}</td>
                        <td class="col-error" :title="b.last_error || ''">{{ b.last_error || '-' }}</td>
                        <td>
                            <label class="toggle" :class="{ on: b.enabled, disabled: busy[b.id] }">
                                <input type="checkbox" :checked="b.enabled" :disabled="busy[b.id]" @change="toggleEnabled(b)">
                                <span class="track"><span class="thumb"></span></span>
                            </label>
                        </td>
                        <td class="col-actions">
                            <button class="btn sm" type="button" @click="editBinding(b)">编辑</button>
                            <button class="btn sm danger" type="button" :disabled="busy[b.id]" @click="removeBinding(b)">删除</button>
                        </td>
                    </tr>
                </tbody>
            </table>

            <form class="form" @submit.prevent="saveBinding">
                <div class="form-row">
                    <label>平台</label>
                    <div class="form-control">
                        <select v-model="bForm.platform" :disabled="!!bForm.id">
                            <option v-for="p in PLATFORMS" :key="p" :value="p">{{ p }}</option>
                        </select>
                    </div>
                </div>
                <div class="form-row">
                    <label>App ID</label>
                    <div class="form-control"><input v-model.trim="bForm.app_id" type="text" required placeholder="cli_xxx"></div>
                </div>
                <div class="form-row">
                    <label>App Secret</label>
                    <div class="form-control">
                        <input v-model.trim="bForm.app_secret" type="password" required
                               :placeholder="bForm.id ? '保持 *** 则不修改' : ''">
                    </div>
                </div>
                <p v-if="bError" class="form-error">{{ bError }}</p>
                <div class="form-actions">
                    <button class="btn primary" type="submit" :disabled="bSaving">
                        {{ bSaving ? '提交中…' : (bForm.id ? '更新 binding' : '添加 binding') }}
                    </button>
                    <button v-if="bForm.id" class="btn" type="button" @click="resetBindingForm">取消编辑</button>
                </div>
            </form>
        </template>
    </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import api from '../api.js';
import StatusDot from '../components/StatusDot.vue';

const CONNECTIONS = ['feishu'];
const PLATFORMS = ['feishu'];
const SECRET_MASK = '***';
const POLL_MS = 5000;

const props = defineProps({ id: { type: String, default: '' } });
const router = useRouter();
const isNew = computed(() => !props.id);

// ---- agent ----
const form = reactive({ name: '', model: '', instructions: '', tools: [], connections: [] });
const models = ref([]);
const tools = ref([]);
const loadError = ref('');
const saveError = ref('');
const saving = ref(false);

async function loadAgent() {
    const a = await api.getAgent(props.id);
    Object.assign(form, {
        name: a.name, model: a.model, instructions: a.instructions,
        tools: [...a.tools], connections: [...a.connections],
    });
}

async function save() {
    saving.value = true;
    saveError.value = '';
    try {
        const body = { ...form };
        if (isNew.value) {
            const a = await api.createAgent(body);
            router.replace(`/agents/${a.id}`);
        } else {
            await api.updateAgent(props.id, body);
        }
    } catch (e) {
        saveError.value = e.message;
    } finally {
        saving.value = false;
    }
}

async function remove() {
    if (!confirm(`删除 agent「${form.name}」？其下所有 binding 会一并删除。`)) return;
    try {
        await api.deleteAgent(props.id);
        router.push('/agents');
    } catch (e) {
        saveError.value = e.message;
    }
}

// ---- bindings ----
const bindings = ref([]);
const busy = reactive({});
const bForm = reactive({ id: '', platform: 'feishu', app_id: '', app_secret: '' });
const bError = ref('');
const bSaving = ref(false);
let pollTimer = null;

async function loadBindings() {
    try {
        bindings.value = await api.listBindings(props.id);
    } catch (e) {
        bError.value = e.message;
    }
}

function resetBindingForm() {
    Object.assign(bForm, { id: '', platform: 'feishu', app_id: '', app_secret: '' });
    bError.value = '';
}

function editBinding(b) {
    Object.assign(bForm, {
        id: b.id, platform: b.platform,
        app_id: b.credentials.app_id ?? '',
        app_secret: b.credentials.app_secret ?? SECRET_MASK,
    });
    bError.value = '';
}

async function saveBinding() {
    bSaving.value = true;
    bError.value = '';
    const credentials = { app_id: bForm.app_id, app_secret: bForm.app_secret };
    try {
        if (bForm.id) {
            await api.updateBinding(bForm.id, { credentials });
        } else {
            await api.createBinding({ agent_id: props.id, platform: bForm.platform, credentials });
        }
        resetBindingForm();
        await loadBindings();
    } catch (e) {
        bError.value = e.message;
    } finally {
        bSaving.value = false;
    }
}

async function toggleEnabled(b) {
    busy[b.id] = true;
    try {
        await api.updateBinding(b.id, { enabled: !b.enabled });
        await loadBindings();
    } catch (e) {
        bError.value = e.message;
    } finally {
        busy[b.id] = false;
    }
}

async function removeBinding(b) {
    if (!confirm(`删除 binding ${b.credentials.app_id}？`)) return;
    busy[b.id] = true;
    try {
        await api.deleteBinding(b.id);
        if (bForm.id === b.id) resetBindingForm();
        await loadBindings();
    } catch (e) {
        bError.value = e.message;
    } finally {
        busy[b.id] = false;
    }
}

// ---- lifecycle ----
onMounted(async () => {
    try {
        [models.value, tools.value] = await Promise.all([api.listModels(), api.listTools()]);
        if (isNew.value) {
            form.model = models.value[0]?.id ?? '';
        } else {
            await loadAgent();
            await loadBindings();
            pollTimer = setInterval(loadBindings, POLL_MS);
        }
    } catch (e) {
        loadError.value = e.message;
    }
});

onBeforeUnmount(() => {
    if (pollTimer) clearInterval(pollTimer);
});
</script>
