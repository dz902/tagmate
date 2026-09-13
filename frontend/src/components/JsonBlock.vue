<template>
    <div class="chip" :class="{ expanded }">
        <div class="chip-header" @click="expanded = !expanded">
            <span class="chip-arrow">&#9654;</span>
            <span class="chip-title">{{ title }}</span>
            <span class="chip-meta">{{ preview }}</span>
        </div>
        <div class="chip-body">
            <pre class="json-block">{{ pretty }}</pre>
        </div>
    </div>
</template>

<script setup>
/* 折叠的 JSON / 文本块。value 为对象时 pretty-print；为字符串时先尝试 JSON.parse，失败则原样显示。 */
import { computed, ref } from 'vue';

const props = defineProps({
    title: { type: String, default: 'JSON' },
    value: { type: [Object, Array, String, Number, Boolean, null], default: null },
    open: { type: Boolean, default: false },
});

const expanded = ref(props.open);

const parsed = computed(() => {
    if (typeof props.value !== 'string') return props.value;
    try { return JSON.parse(props.value); } catch { return props.value; }
});

const pretty = computed(() =>
    typeof parsed.value === 'string' ? parsed.value : JSON.stringify(parsed.value, null, 2));

const preview = computed(() => {
    const s = typeof parsed.value === 'string' ? parsed.value : JSON.stringify(parsed.value);
    return s && s.length > 60 ? s.slice(0, 60) + '…' : s;
});
</script>
