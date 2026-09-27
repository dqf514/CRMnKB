import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/index.css'
import './styles/markdown.css'
import App from './App.vue'
import router from './router'
import { useThemeStore } from './stores/theme'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
// 挂载前应用本地缓存主题，登录页/未登录状态同样生效
useThemeStore().apply()
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
