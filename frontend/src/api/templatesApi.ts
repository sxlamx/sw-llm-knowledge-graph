import { api } from './baseApi';

export interface TemplateSummary {
  key: string;
  name: string;
  domain: string;
  type: string;
  description: string;
}

export interface FieldDef {
  name: string;
  type: string;
  description: string;
  required: boolean;
  default?: unknown;
}

export interface TemplateDetail {
  name: string;
  type: string;
  language: string[];
  domain: string;
  description: string;
  entity_schema?: {
    fields: FieldDef[];
    key: string;
    display_label: string;
  };
  relation_schema?: {
    fields: FieldDef[];
    key: string;
    source_field: string;
    target_field: string;
    display_label: string;
    participants_field?: string;
  };
  identifiers?: Record<string, string>;
  extraction: {
    mode: string;
    method: string;
    merge_strategy_nodes: string;
    merge_strategy_edges: string;
  };
}

export interface ExtractionMethod {
  name: string;
  auto_type: string;
  description: string;
  implemented: boolean;
}

export const templatesApi = api.injectEndpoints({
  endpoints: (builder) => ({
    listTemplates: builder.query<TemplateSummary[], { domain?: string; type?: string } | void>({
      query: (params) => {
        const searchParams = new URLSearchParams();
        if (params && typeof params === 'object') {
          if (params.domain) searchParams.set('domain', params.domain);
          if (params.type) searchParams.set('type_filter', params.type);
        }
        const qs = searchParams.toString();
        return `/templates${qs ? `?${qs}` : ''}`;
      },
      providesTags: ['Template'],
    }),
    getTemplate: builder.query<TemplateDetail, string>({
      query: (key) => `/templates/${key}`,
      providesTags: (_r, _e, key) => [{ type: 'Template', id: key }],
    }),
    listExtractionMethods: builder.query<ExtractionMethod[], { implemented_only?: boolean } | void>({
      query: (params) => {
        const searchParams = new URLSearchParams();
        if (params?.implemented_only === false) searchParams.set('implemented_only', 'false');
        const qs = searchParams.toString();
        return `/templates/extraction-methods${qs ? `?${qs}` : ''}`;
      },
      providesTags: ['ExtractionMethod'],
    }),
  }),
});

export const { useListTemplatesQuery, useGetTemplateQuery, useListExtractionMethodsQuery } = templatesApi;