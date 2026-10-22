<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
const props = defineProps<{ to: string }>();
const route = useRoute();
const router = useRouter();
const selected = computed(() => {
  const target = router.resolve(props.to);
  if (route.path === "/scheduling") return target.path === "/calendar";
  if (route.path.startsWith("/assistant")) return props.to === "/assistant";
  const path = /^\/tickets\/\d+$/.test(route.path) ? "/tickets" : route.path;
  return (
    path === target.path &&
    (route.query.scope || "") === (target.query.scope || "")
  );
});
</script>
<template>
  <RouterLink :to="to" custom v-slot="{ href, navigate }">
    <a
      class="nav-item"
      :href="href"
      :aria-current="selected ? 'page' : undefined"
      @click="navigate"
      ><slot
    /></a>
  </RouterLink>
</template>
