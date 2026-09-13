import { createApp } from 'vue';
import 'modern-normalize';
import './styles/base.scss';
import './styles/logs.scss';
import App from './App.vue';
import router from './router.js';

createApp(App).use(router).mount('#app');
