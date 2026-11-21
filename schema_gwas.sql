--
-- PostgreSQL database dump
--

\restrict PYs9QzPHyAJzNL25vTudULYCnQAOrcX7ioqohBrVWcORhfeQZGHGZtCZ2YkKc5N

-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: accession; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accession (
    accession_id integer NOT NULL,
    sample_code character varying(100),
    organism character varying(100),
    source_domain character varying(100),
    genotype_file_path character varying(255)
);


--
-- Name: accession_accession_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.accession_accession_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: accession_accession_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.accession_accession_id_seq OWNED BY public.accession.accession_id;


--
-- Name: gwas_analysis; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gwas_analysis (
    analysis_id integer NOT NULL,
    accession_id integer NOT NULL,
    analysis_mode character varying(50),
    qc_skipped boolean DEFAULT false,
    pca_used boolean DEFAULT false,
    model_used character varying(50),
    status character varying(50),
    user_id integer
);


--
-- Name: gwas_analysis_analysis_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gwas_analysis_analysis_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gwas_analysis_analysis_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gwas_analysis_analysis_id_seq OWNED BY public.gwas_analysis.analysis_id;


--
-- Name: gwas_result; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gwas_result (
    result_id integer NOT NULL,
    analysis_id integer NOT NULL,
    snp_id integer NOT NULL,
    p_value numeric(15,10),
    effect_size numeric(15,10),
    is_significant boolean DEFAULT false
);


--
-- Name: gwas_result_result_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gwas_result_result_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gwas_result_result_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gwas_result_result_id_seq OWNED BY public.gwas_result.result_id;


--
-- Name: phenotype; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.phenotype (
    phenotype_id integer NOT NULL,
    accession_id integer NOT NULL,
    trait_name character varying(100),
    trait_type character varying(50),
    trait_value character varying(100)
);


--
-- Name: phenotype_phenotype_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.phenotype_phenotype_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: phenotype_phenotype_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.phenotype_phenotype_id_seq OWNED BY public.phenotype.phenotype_id;


--
-- Name: snp; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.snp (
    snp_id integer NOT NULL,
    rsid character varying(100),
    chromosome character varying(50),
    "position" integer,
    ref_allele character varying(10),
    alt_allele character varying(10)
);


--
-- Name: snp_snp_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.snp_snp_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: snp_snp_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.snp_snp_id_seq OWNED BY public.snp.snp_id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    user_id integer NOT NULL,
    full_name character varying(100) NOT NULL,
    email character varying(255) NOT NULL,
    username character varying(50),
    password_hash character varying(255) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_email_lowercase CHECK (((email)::text = lower((email)::text))),
    CONSTRAINT users_username_lowercase CHECK (((username IS NULL) OR ((username)::text = lower((username)::text))))
);


--
-- Name: users_user_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_user_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_user_id_seq OWNED BY public.users.user_id;


--
-- Name: accession accession_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accession ALTER COLUMN accession_id SET DEFAULT nextval('public.accession_accession_id_seq'::regclass);


--
-- Name: gwas_analysis analysis_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gwas_analysis ALTER COLUMN analysis_id SET DEFAULT nextval('public.gwas_analysis_analysis_id_seq'::regclass);


--
-- Name: gwas_result result_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gwas_result ALTER COLUMN result_id SET DEFAULT nextval('public.gwas_result_result_id_seq'::regclass);


--
-- Name: phenotype phenotype_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.phenotype ALTER COLUMN phenotype_id SET DEFAULT nextval('public.phenotype_phenotype_id_seq'::regclass);


--
-- Name: snp snp_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.snp ALTER COLUMN snp_id SET DEFAULT nextval('public.snp_snp_id_seq'::regclass);


--
-- Name: users user_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN user_id SET DEFAULT nextval('public.users_user_id_seq'::regclass);


--
-- Name: accession accession_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accession
    ADD CONSTRAINT accession_pkey PRIMARY KEY (accession_id);


--
-- Name: gwas_analysis gwas_analysis_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gwas_analysis
    ADD CONSTRAINT gwas_analysis_pkey PRIMARY KEY (analysis_id);


--
-- Name: gwas_result gwas_result_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gwas_result
    ADD CONSTRAINT gwas_result_pkey PRIMARY KEY (result_id);


--
-- Name: phenotype phenotype_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.phenotype
    ADD CONSTRAINT phenotype_pkey PRIMARY KEY (phenotype_id);


--
-- Name: snp snp_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.snp
    ADD CONSTRAINT snp_pkey PRIMARY KEY (snp_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: idx_gwas_analysis_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gwas_analysis_user_id ON public.gwas_analysis USING btree (user_id);


--
-- Name: gwas_analysis fk_analysis_accession; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gwas_analysis
    ADD CONSTRAINT fk_analysis_accession FOREIGN KEY (accession_id) REFERENCES public.accession(accession_id) ON DELETE CASCADE;


--
-- Name: phenotype fk_phenotype_accession; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.phenotype
    ADD CONSTRAINT fk_phenotype_accession FOREIGN KEY (accession_id) REFERENCES public.accession(accession_id) ON DELETE CASCADE;


--
-- Name: gwas_result fk_result_analysis; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gwas_result
    ADD CONSTRAINT fk_result_analysis FOREIGN KEY (analysis_id) REFERENCES public.gwas_analysis(analysis_id) ON DELETE CASCADE;


--
-- Name: gwas_result fk_result_snp; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gwas_result
    ADD CONSTRAINT fk_result_snp FOREIGN KEY (snp_id) REFERENCES public.snp(snp_id) ON DELETE CASCADE;


--
-- Name: gwas_analysis gwas_analysis_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gwas_analysis
    ADD CONSTRAINT gwas_analysis_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- PostgreSQL database dump complete
--

\unrestrict PYs9QzPHyAJzNL25vTudULYCnQAOrcX7ioqohBrVWcORhfeQZGHGZtCZ2YkKc5N

