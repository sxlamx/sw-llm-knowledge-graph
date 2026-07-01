//! LanceDB table operations, schema builders, and RecordBatch construction.

use arrow_array::{
    FixedSizeListArray, Float32Array, Int32Array, ListArray, RecordBatch, StringArray,
    TimestampMicrosecondArray,
};
use arrow_schema::{DataType, Field, Schema, TimeUnit};
use std::sync::Arc;

const DEFAULT_EMBEDDING_DIM: i32 = 1024;

pub fn chunks_schema(dim: Option<i32>) -> Schema {
    let dim = dim.unwrap_or(DEFAULT_EMBEDDING_DIM);
    Schema::new(vec![
        Field::new("id", DataType::Utf8, false),
        Field::new("doc_id", DataType::Utf8, false),
        Field::new("collection_id", DataType::Utf8, false),
        Field::new("text", DataType::Utf8, false),
        Field::new("contextual_text", DataType::Utf8, false),
        Field::new(
            "embedding",
            DataType::FixedSizeList(Arc::new(Field::new("item", DataType::Float32, true)), dim),
            false,
        ),
        Field::new("position", DataType::Int32, false),
        Field::new("token_count", DataType::Int32, true),
        Field::new("page", DataType::Int32, true),
        Field::new(
            "topics",
            DataType::List(Arc::new(Field::new("item", DataType::Utf8, true))),
            true,
        ),
        Field::new(
            "created_at",
            DataType::Timestamp(TimeUnit::Microsecond, Some("UTC".into())),
            false,
        ),
    ])
}

pub fn nodes_schema(dim: Option<i32>) -> Schema {
    let dim = dim.unwrap_or(DEFAULT_EMBEDDING_DIM);
    Schema::new(vec![
        Field::new("id", DataType::Utf8, false),
        Field::new("collection_id", DataType::Utf8, false),
        Field::new("label", DataType::Utf8, false),
        Field::new("entity_type", DataType::Utf8, false),
        Field::new("description", DataType::Utf8, true),
        Field::new(
            "aliases",
            DataType::List(Arc::new(Field::new("item", DataType::Utf8, true))),
            true,
        ),
        Field::new(
            "embedding",
            DataType::FixedSizeList(Arc::new(Field::new("item", DataType::Float32, true)), dim),
            true,
        ),
        Field::new("confidence", DataType::Float32, false),
        Field::new("ontology_class", DataType::Utf8, true),
        Field::new("metadata", DataType::Utf8, true),
        Field::new(
            "created_at",
            DataType::Timestamp(TimeUnit::Microsecond, Some("UTC".into())),
            false,
        ),
        Field::new(
            "updated_at",
            DataType::Timestamp(TimeUnit::Microsecond, Some("UTC".into())),
            false,
        ),
    ])
}

pub fn edges_schema() -> Schema {
    Schema::new(vec![
        Field::new("id", DataType::Utf8, false),
        Field::new("collection_id", DataType::Utf8, false),
        Field::new("source_id", DataType::Utf8, false),
        Field::new("target_id", DataType::Utf8, false),
        Field::new("predicate", DataType::Utf8, false),
        Field::new("weight", DataType::Float32, false),
        Field::new("context", DataType::Utf8, true),
        Field::new("chunk_id", DataType::Utf8, true),
        Field::new(
            "doc_origins",
            DataType::List(Arc::new(Field::new("item", DataType::Utf8, true))),
            true,
        ),
        Field::new(
            "created_at",
            DataType::Timestamp(TimeUnit::Microsecond, Some("UTC".into())),
            false,
        ),
    ])
}

pub fn documents_schema() -> Schema {
    Schema::new(vec![
        Field::new("id", DataType::Utf8, false),
        Field::new("collection_id", DataType::Utf8, false),
        Field::new("title", DataType::Utf8, false),
        Field::new("source", DataType::Utf8, false),
        Field::new("path", DataType::Utf8, false),
        Field::new("file_type", DataType::Utf8, false),
        Field::new("file_hash", DataType::Utf8, true),
        Field::new("raw_content", DataType::LargeBinary, true),
        Field::new("doc_summary", DataType::Utf8, true),
        Field::new("metadata", DataType::Utf8, true),
        Field::new(
            "created_at",
            DataType::Timestamp(TimeUnit::Microsecond, Some("UTC".into())),
            false,
        ),
        Field::new(
            "updated_at",
            DataType::Timestamp(TimeUnit::Microsecond, Some("UTC".into())),
            false,
        ),
    ])
}

pub fn topics_schema(dim: Option<i32>) -> Schema {
    let dim = dim.unwrap_or(DEFAULT_EMBEDDING_DIM);
    Schema::new(vec![
        Field::new("id", DataType::Utf8, false),
        Field::new("collection_id", DataType::Utf8, false),
        Field::new("name", DataType::Utf8, false),
        Field::new(
            "embedding",
            DataType::FixedSizeList(Arc::new(Field::new("item", DataType::Float32, true)), dim),
            true,
        ),
        Field::new(
            "keywords",
            DataType::List(Arc::new(Field::new("item", DataType::Utf8, true))),
            true,
        ),
        Field::new("score", DataType::Float32, true),
        Field::new("frequency", DataType::Int32, true),
    ])
}

pub fn build_chunks_record_batch(
    records: &[crate::models::ChunkRecord],
    dim: i32,
) -> Result<RecordBatch, String> {
    let schema = chunks_schema(Some(dim));
    let n = records.len();

    let ids: Vec<&str> = records.iter().map(|r| r.id.as_str()).collect();
    let doc_ids: Vec<&str> = records.iter().map(|r| r.doc_id.as_str()).collect();
    let collection_ids: Vec<&str> = records.iter().map(|r| r.collection_id.as_str()).collect();
    let texts: Vec<&str> = records.iter().map(|r| r.text.as_str()).collect();
    let contextual_texts: Vec<&str> = records.iter().map(|r| r.contextual_text.as_str()).collect();

    let flat_embeddings: Vec<f32> = records.iter().flat_map(|r| r.embedding.iter().copied()).collect();
    let embedding_values = Float32Array::from(flat_embeddings);
    let embeddings = FixedSizeListArray::try_new(
        Arc::new(Field::new("item", DataType::Float32, true)),
        dim,
        Arc::new(embedding_values),
        None,
    )
    .map_err(|e| e.to_string())?;

    let positions: Vec<i32> = records.iter().map(|r| r.position).collect();
    let token_counts: Vec<Option<i32>> = records.iter().map(|r| r.token_count).collect();
    let pages: Vec<Option<i32>> = records.iter().map(|r| r.page).collect();

    let topics_offsets: Vec<i32> = {
        let mut offsets = Vec::with_capacity(n + 1);
        offsets.push(0i32);
        for r in records {
            offsets.push(offsets.last().unwrap() + r.topics.len() as i32);
        }
        offsets
    };
    let topics_values: Vec<Option<&str>> = records
        .iter()
        .flat_map(|r| {
            if r.topics.is_empty() {
                vec![]
            } else {
                r.topics.iter().map(|t| Some(t.as_str())).collect()
            }
        })
        .collect();
    let topics_list = ListArray::try_new(
        Arc::new(Field::new("item", DataType::Utf8, true)),
        arrow::buffer::OffsetBuffer::new(arrow::buffer::ScalarBuffer::from(topics_offsets)),
        Arc::new(StringArray::from(topics_values)),
        None,
    )
    .map_err(|e| e.to_string())?;

    let created_ats: Vec<i64> = records.iter().map(|r| r.created_at).collect();
    let timestamps = TimestampMicrosecondArray::from(created_ats)
        .with_timezone("UTC");

    RecordBatch::try_new(
        Arc::new(schema),
        vec![
            Arc::new(StringArray::from(ids)),
            Arc::new(StringArray::from(doc_ids)),
            Arc::new(StringArray::from(collection_ids)),
            Arc::new(StringArray::from(texts)),
            Arc::new(StringArray::from(contextual_texts)),
            Arc::new(embeddings),
            Arc::new(Int32Array::from(positions)),
            Arc::new(Int32Array::from(token_counts)),
            Arc::new(Int32Array::from(pages)),
            Arc::new(topics_list),
            Arc::new(timestamps),
        ],
    )
    .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::ChunkRecord;

    #[test]
    fn test_chunks_schema_has_expected_fields() {
        let schema = chunks_schema(None);
        let field_names: Vec<&str> = schema.fields().iter().map(|f| f.name().as_str()).collect();
        assert!(field_names.contains(&"id"));
        assert!(field_names.contains(&"embedding"));
        assert!(field_names.contains(&"topics"));
        assert!(field_names.contains(&"created_at"));
    }

    #[test]
    fn test_chunks_schema_embedding_is_fixed_size_list() {
        let schema = chunks_schema(None);
        let embedding_field = schema.field_with_name("embedding").unwrap();
        match embedding_field.data_type() {
            DataType::FixedSizeList(_, dim) => assert_eq!(*dim, 1024),
            other => panic!("Expected FixedSizeList, got {:?}", other),
        }
    }

    #[test]
    fn test_chunks_schema_custom_dimension() {
        let schema = chunks_schema(Some(768));
        let embedding_field = schema.field_with_name("embedding").unwrap();
        match embedding_field.data_type() {
            DataType::FixedSizeList(_, dim) => assert_eq!(*dim, 768),
            other => panic!("Expected FixedSizeList, got {:?}", other),
        }
    }

    #[test]
    fn test_nodes_schema_has_expected_fields() {
        let schema = nodes_schema(None);
        let field_names: Vec<&str> = schema.fields().iter().map(|f| f.name().as_str()).collect();
        assert!(field_names.contains(&"id"));
        assert!(field_names.contains(&"label"));
        assert!(field_names.contains(&"entity_type"));
        assert!(field_names.contains(&"embedding"));
    }

    #[test]
    fn test_edges_schema_has_expected_fields() {
        let schema = edges_schema();
        let field_names: Vec<&str> = schema.fields().iter().map(|f| f.name().as_str()).collect();
        assert!(field_names.contains(&"id"));
        assert!(field_names.contains(&"source_id"));
        assert!(field_names.contains(&"target_id"));
        assert!(field_names.contains(&"predicate"));
        assert!(field_names.contains(&"weight"));
    }

    #[test]
    fn test_documents_schema_has_expected_fields() {
        let schema = documents_schema();
        let field_names: Vec<&str> = schema.fields().iter().map(|f| f.name().as_str()).collect();
        assert!(field_names.contains(&"id"));
        assert!(field_names.contains(&"title"));
        assert!(field_names.contains(&"raw_content"));
        assert!(field_names.contains(&"file_hash"));
    }

    #[test]
    fn test_topics_schema_has_expected_fields() {
        let schema = topics_schema(None);
        let field_names: Vec<&str> = schema.fields().iter().map(|f| f.name().as_str()).collect();
        assert!(field_names.contains(&"id"));
        assert!(field_names.contains(&"name"));
        assert!(field_names.contains(&"embedding"));
        assert!(field_names.contains(&"keywords"));
    }

    #[test]
    fn test_build_chunks_record_batch_creates_valid_batch() {
        let records = vec![ChunkRecord {
            id: uuid::Uuid::new_v4().to_string(),
            doc_id: uuid::Uuid::new_v4().to_string(),
            collection_id: uuid::Uuid::new_v4().to_string(),
            text: "hello world".to_string(),
            contextual_text: "prefix hello world".to_string(),
            embedding: vec![0.1f32; 1024],
            position: 0,
            token_count: Some(3),
            page: Some(1),
            topics: vec!["ai".to_string()],
            created_at: 1700000000000i64,
        }];

        let batch = build_chunks_record_batch(&records, 1024).unwrap();
        assert_eq!(batch.num_rows(), 1);
        assert_eq!(batch.num_columns(), 11);
    }

    #[test]
    fn test_build_chunks_record_batch_multiple_records() {
        let records: Vec<ChunkRecord> = (0..5)
            .map(|i| ChunkRecord {
                id: uuid::Uuid::new_v4().to_string(),
                doc_id: uuid::Uuid::new_v4().to_string(),
                collection_id: uuid::Uuid::new_v4().to_string(),
                text: format!("chunk text {}", i),
                contextual_text: format!("context chunk text {}", i),
                embedding: vec![0.0f32; 1024],
                position: i,
                token_count: Some(i + 1),
                page: Some(1),
                topics: vec![],
                created_at: 1700000000000 + i as i64,
            })
            .collect();

        let batch = build_chunks_record_batch(&records, 1024).unwrap();
        assert_eq!(batch.num_rows(), 5);
    }

    #[test]
    fn test_build_chunks_record_batch_empty_topics() {
        let records = vec![ChunkRecord {
            id: uuid::Uuid::new_v4().to_string(),
            doc_id: uuid::Uuid::new_v4().to_string(),
            collection_id: uuid::Uuid::new_v4().to_string(),
            text: "test".to_string(),
            contextual_text: "test".to_string(),
            embedding: vec![0.0f32; 1024],
            position: 0,
            token_count: None,
            page: None,
            topics: vec![],
            created_at: 1700000000000i64,
        }];

        let batch = build_chunks_record_batch(&records, 1024).unwrap();
        assert_eq!(batch.num_rows(), 1);
    }
}