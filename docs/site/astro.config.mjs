// @ts-check
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightLinksValidator from 'starlight-links-validator';

// A fork's CI builds for its own Pages URL; the path stays /nani-pix-bot,
// which content links hard-code (see the spec), so forks keep the repo name.
const owner = process.env.GITHUB_REPOSITORY_OWNER ?? 'sm-steel';
const repoRoot = fileURLToPath(new URL('../..', import.meta.url));

export default defineConfig({
  site: `https://${owner}.github.io`,
  base: '/nani-pix-bot',
  // Components import the bot's locale and release-notes files from outside docs/site.
  vite: { server: { fs: { allow: [repoRoot] } } },
  integrations: [
    starlight({
      title: { en: 'nani-pix-bot guide', ru: 'Гайд по nani-pix-bot' },
      description: 'Anime-screenshot guessing game for Telegram group chats',
      defaultLocale: 'root',
      locales: {
        root: { label: 'English', lang: 'en' },
        ru: { label: 'Русский', lang: 'ru' },
      },
      favicon: '/favicon.svg',
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/sm-steel/nani-pix-bot' },
      ],
      sidebar: [
        { label: 'Players', translations: { ru: 'Игрокам' }, items: [{ autogenerate: { directory: 'players' } }] },
        { label: 'Group admins', translations: { ru: 'Админам группы' }, items: [{ autogenerate: { directory: 'admins' } }] },
        { label: 'Self-hosting', translations: { ru: 'Свой сервер' }, items: [{ autogenerate: { directory: 'self-hosting' } }] },
        { label: "What's new", translations: { ru: 'Что нового' }, link: '/whats-new/' },
      ],
      customCss: [
        '@fontsource-variable/inter',
        './src/styles/theme.css',
      ],
      components: { Head: './src/components/overrides/Head.astro' },
      plugins: [starlightLinksValidator()],
    }),
  ],
});
