<template>
    <div id="app-body">
        <nav id="nav">
            <div class="nav-title">TagMate</div>
            <RouterLink v-for="item in navItems" :key="item.to" class="nav-item" :to="item.to">
                {{ item.label }}
            </RouterLink>
        </nav>
        <main id="main">
            <RouterView />
        </main>
    </div>
</template>

<script setup>
const navItems = [
    { to: '/agents', label: 'Agents' },
    { to: '/bindings', label: 'Bindings' },
    { to: '/logs', label: 'Logs' },
];
</script>
