<template>
    <div class="view">
        <div class="view-header">
            <h2>Agents</h2>
            <span class="spacer"></span>
            <button class="btn primary" @click="router.push('/agents/new')">新建</button>
        </div>

        <p v-if="error" class="form-error">{{ error }}</p>
        <div v-else-if="loaded && agents.length === 0" class="empty-state">还没有 agent，点“新建”创建第一个。</div>

        <table v-else-if="loaded" class="table">
            <thead>
                <tr>
                    <th>名称</th>
                    <th>模型</th>
                    <th class="col-num">Tools</th>
                    <th class="col-num">Bindings</th>
                    <th>更新时间</th>
                </tr>
            </thead>
            <tbody>
                <tr v-for="a in agents" :key="a.id" class="clickable" @click="router.push(`/agents/${a.id}`)">
                    <td class="col-name">{{ a.name }}</td>
                    <td class="col-mono col-muted">{{ modelName(a.model) }}</td>
                    <td class="col-num">{{ a.tools.length }}</td>
                    <td class="col-num">{{ bindingCount[a.id] ?? 0 }}</td>
                    <td class="col-muted">{{ fmtTime(a.updated_at) }}</td>
                </tr>
            </tbody>
        </table>
    </div>
</template>

<script setup>
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import api from '../api.js';
import { fmtTime } from '../format.js';

const router = useRouter();
const agents = ref([]);
const bindingCount = ref({});
const models = ref([]);
const loaded = ref(false);
const error = ref('');

function modelName(id) {
    if (!id) return '默认';
    return models.value.find((m) => m.id === id)?.name ?? id;
}

onMounted(async () => {
    try {
        const [as, bs, ms] = await Promise.all([api.listAgents(), api.listBindings(), api.listModels()]);
        agents.value = as;
        models.value = ms;
        const counts = {};
        for (const b of bs) counts[b.agent_id] = (counts[b.agent_id] ?? 0) + 1;
        bindingCount.value = counts;
    } catch (e) {
        error.value = e.message;
    } finally {
        loaded.value = true;
    }
});
</script>
