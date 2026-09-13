<template>
    <span class="status-cell" :title="status">
        <span class="status-dot" :class="status"></span>
        <span v-if="label">{{ statusLabel(status) }}</span>
    </span>
</template>

<script setup>
import { statusLabel } from '../format.js';

defineProps({
    status: { type: String, default: 'disconnected' },
    label: { type: Boolean, default: true },
});
</script>
