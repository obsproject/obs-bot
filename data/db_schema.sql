CREATE TABLE "factoids"
(
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "name" text NOT NULL,
    aliases text[] DEFAULT '{}',
    "message" text NOT NULL,
    image_url text,
    embed BOOL DEFAULT false,
    uses integer DEFAULT 0
);

CREATE TABLE "hardware_stats"
(
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    gpu_id integer,
    cpu_id integer,
    name text NOT NULL,
    counts integer DEFAULT 0
);

CREATE TABLE "commit_messages"
(
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    commit_hash varchar(40) NOT NULL,
    channel_id numeric NOT NULL,
    message_id numeric NOT NULL
);

