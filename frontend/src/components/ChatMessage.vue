<script setup lang="ts">
import { computed } from "vue";
import { messageBlocks, inlineParts } from "../chat";
const props = defineProps<{ text: string }>();
const blocks = computed(() => messageBlocks(props.text));
</script>
<template>
  <div class="ai-formatted">
    <template v-for="(block, i) in blocks" :key="i">
      <div v-if="block.kind === 'code'" class="ai-code">
        <div v-if="block.language" class="ai-code-label">
          {{ block.language }}
        </div>
        <pre><code>{{ block.content }}</code></pre>
      </div>
      <div v-else class="ai-prose">
        <p
          v-for="(line, j) in block.content.split('\n')"
          :key="j"
          :class="{
            'ai-prose-heading': /^#{1,3} /.test(line),
            'ai-prose-bullet': /^[-*] /.test(line),
          }"
        >
          <template
            v-for="(part, k) in inlineParts(
              line.replace(/^#{1,3} /, '').replace(/^[-*] /, '• '),
            )"
            :key="k"
            ><strong v-if="part.kind === 'bold'">{{ part.text }}</strong
            ><code v-else-if="part.kind === 'code'">{{ part.text }}</code
            ><template v-else>{{ part.text }}</template></template
          ><br v-if="!line" />
        </p>
      </div>
    </template>
  </div>
</template>
