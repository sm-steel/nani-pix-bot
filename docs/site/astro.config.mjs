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
      title: { en: 'Nani Pix guide', ru: 'Гайд по Nani Pix' },
      description: 'Anime-screenshot guessing game for Telegram group chats',
      logo: { src: './src/assets/mascot/emblem.webp', alt: 'Nani Pix' },
      head: [
        { tag: 'link', attrs: { rel: 'apple-touch-icon', href: '/nani-pix-bot/apple-touch-icon.png' } },
      ],
      defaultLocale: 'root',
      locales: {
        root: { label: 'English', lang: 'en' },
        ru: { label: 'Русский', lang: 'ru' },
      },
      favicon: '/favicon.png',
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/sm-steel/nani-pix-bot' },
      ],
      sidebar: [
        {
          label: 'Players',
          translations: { ru: 'Игрокам' },
          items: [
            { autogenerate: { directory: 'players' } },
            { label: 'Seasons', translations: { ru: 'Сезоны' }, items: [{ autogenerate: { directory: 'seasons' } }] },
          ],
        },
        { label: 'Group admins', translations: { ru: 'Админам группы' }, items: [{ autogenerate: { directory: 'admins' } }] },
        { label: 'Self-hosting', translations: { ru: 'Свой сервер' }, items: [{ autogenerate: { directory: 'self-hosting' } }] },
        { label: "What's new", translations: { ru: 'Что нового' }, link: '/whats-new/' },
        { label: 'License', translations: { ru: 'Лицензия' }, link: '/license/' },
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
