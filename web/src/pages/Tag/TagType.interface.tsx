export interface TagType {
    description: string;
    required: string; // string coming from api, not boolean
    tagTypeName: string;
    tags: string[];
}
