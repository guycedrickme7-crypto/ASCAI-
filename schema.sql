-- =====================================================================
-- ASCAI : base de données des rapports de vente (Supabase)
-- À coller dans Supabase > SQL Editor > New query, puis cliquer sur « Run ».
-- Peut être relancé sans danger.
-- =====================================================================

-- 1. Profils : un par compte (agent ou responsable) ---------------------
create table if not exists public.profils (
  id      uuid primary key references auth.users(id) on delete cascade,
  nom     text not null check (char_length(nom) between 1 and 80),
  role    text not null default 'agent' check (role in ('agent','responsable')),
  actif   boolean not null default true,
  cree_le timestamptz not null default now()
);
alter table public.profils enable row level security;

create or replace function public.est_responsable() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profils where id = auth.uid() and role = 'responsable' and actif);
$$;
create or replace function public.est_actif() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profils where id = auth.uid() and actif);
$$;

-- Chaque nouveau compte reçoit automatiquement un profil « agent »
create or replace function public.nouveau_profil() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profils (id, nom)
  values (new.id, left(coalesce(nullif(new.raw_user_meta_data->>'nom',''), split_part(new.email,'@',1)), 80))
  on conflict (id) do nothing;
  return new;
end; $$;
drop trigger if exists a_la_creation_utilisateur on auth.users;
create trigger a_la_creation_utilisateur after insert on auth.users
  for each row execute function public.nouveau_profil();

drop policy if exists profil_lecture on public.profils;
create policy profil_lecture on public.profils for select to authenticated
  using (id = auth.uid() or public.est_responsable());
drop policy if exists profil_gestion on public.profils;
create policy profil_gestion on public.profils for update to authenticated
  using (public.est_responsable()) with check (public.est_responsable());

-- 2. Rapports de vente ---------------------------------------------------
create table if not exists public.rapports (
  id         text primary key check (id ~ '^RAP-[A-Z0-9]{4,20}$'),
  agent_id   uuid not null default auth.uid() references auth.users(id),
  agent_nom  text not null default '',
  client     text not null check (char_length(client) between 1 and 160),
  date_vente timestamptz not null,
  lignes     jsonb not null default '[]'::jsonb check (jsonb_typeof(lignes) = 'array' and jsonb_array_length(lignes) <= 60),
  montant    bigint not null check (montant between 0 and 10000000000),
  mode       text not null check (mode in ('Espèces','Mobile Money','Virement','Crédit (à encaisser)')),
  estime     bigint not null default 0 check (estime between 0 and 10000000000),
  rien       boolean not null default false,
  note       text not null default '' check (char_length(note) <= 600),
  photos     jsonb not null default '[]'::jsonb check (jsonb_typeof(photos) = 'array' and jsonb_array_length(photos) <= 8),
  cree_le    timestamptz not null default now()
);
create index if not exists rapports_date_idx  on public.rapports (date_vente desc);
create index if not exists rapports_agent_idx on public.rapports (agent_id);
alter table public.rapports enable row level security;

-- Le nom de l'agent et l'heure d'enregistrement sont fixés par le serveur (impossible à falsifier)
create or replace function public.rapport_avant_ajout() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  new.agent_id  := auth.uid();
  new.agent_nom := coalesce((select nom from public.profils where id = auth.uid()), '');
  new.cree_le   := now();
  return new;
end; $$;
drop trigger if exists rapport_avant_ajout on public.rapports;
create trigger rapport_avant_ajout before insert on public.rapports
  for each row execute function public.rapport_avant_ajout();

drop policy if exists rapport_ajout on public.rapports;
create policy rapport_ajout on public.rapports for insert to authenticated
  with check (agent_id = auth.uid() and public.est_actif());
drop policy if exists rapport_lecture on public.rapports;
create policy rapport_lecture on public.rapports for select to authenticated
  using (agent_id = auth.uid() or public.est_responsable());
drop policy if exists rapport_suppression on public.rapports;
create policy rapport_suppression on public.rapports for delete to authenticated
  using (public.est_responsable());

-- 3. Photos des produits restants (dossier privé) ------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('photos', 'photos', false, 3000000, array['image/jpeg'])
on conflict (id) do update set public = false, file_size_limit = 3000000, allowed_mime_types = array['image/jpeg'];

drop policy if exists photos_envoi on storage.objects;
create policy photos_envoi on storage.objects for insert to authenticated
  with check (bucket_id = 'photos' and (storage.foldername(name))[1] = auth.uid()::text and public.est_actif());
drop policy if exists photos_maj on storage.objects;
create policy photos_maj on storage.objects for update to authenticated
  using (bucket_id = 'photos' and (storage.foldername(name))[1] = auth.uid()::text and public.est_actif())
  with check (bucket_id = 'photos' and (storage.foldername(name))[1] = auth.uid()::text and public.est_actif());
drop policy if exists photos_lecture on storage.objects;
create policy photos_lecture on storage.objects for select to authenticated
  using (bucket_id = 'photos' and ((storage.foldername(name))[1] = auth.uid()::text or public.est_responsable()));

-- 4. À FAIRE UNE SEULE FOIS, après avoir créé votre propre compte ---------
-- Remplacez l'adresse ci-dessous par l'identifiant de VOTRE compte, puis exécutez cette ligne :
--
-- update public.profils set role = 'responsable', nom = 'Votre nom'
--   where id = (select id from auth.users where email = 'votre-identifiant@ascai.ci');

-- 5. Rattrapage : crée le profil des comptes déjà existants avant ce script -
insert into public.profils (id, nom)
select id, left(split_part(email, '@', 1), 80) from auth.users
on conflict (id) do nothing;
