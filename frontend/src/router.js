import { createRouter, createWebHashHistory } from 'vue-router';
import Agents from './views/Agents.vue';
import AgentEdit from './views/AgentEdit.vue';
import Bindings from './views/Bindings.vue';
import Logs from './views/Logs.vue';

export default createRouter({
    history: createWebHashHistory(),
    routes: [
        { path: '/', redirect: '/agents' },
        { path: '/agents', component: Agents },
        { path: '/agents/new', component: AgentEdit },
        { path: '/agents/:id', component: AgentEdit, props: true },
        { path: '/bindings', component: Bindings },
        { path: '/logs', component: Logs },
    ],
});
